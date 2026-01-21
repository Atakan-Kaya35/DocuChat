"""
Agent API views.

Provides the bounded agent endpoint for multi-step question answering.
Uses executor_v2 with validation gates and constraint enforcement.
"""
import json
import logging

from django.http import JsonResponse, StreamingHttpResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from apps.authn.middleware import auth_required
from apps.authn.ratelimit import rate_limited, check_ask_rate_limit
from apps.authn.audit import log_audit_from_request, AuditEvent
from apps.rag.query_rewriter import rewrite_query

# Use executor_v2 with validation gates
from apps.agent.executor_v2 import (
    run_agent_v2 as run_agent, 
    run_agent_v2_streaming as run_agent_streaming, 
    AgentError, 
    AgentResult,
    TraceEntry,
    MAX_QUESTION_LENGTH
)

logger = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(auth_required, name='dispatch')
@method_decorator(rate_limited(check_ask_rate_limit), name='dispatch')
class AgentRunView(View):
    """
    POST /api/agent/run
    
    Execute bounded agent loop with planning and tool use.
    
    Request body:
        {
            "question": "What are the key findings?",
            "mode": "agent",         // optional, default "agent"
            "returnTrace": true      // optional, default false
        }
    
    Response:
        {
            "answer": "...",
            "citations": [...],
            "trace": [...]           // only if returnTrace=true
        }
    """
    
    def post(self, request):
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        
        # Extract parameters
        question = body.get("question", "")
        mode = body.get("mode", "agent")
        return_trace = body.get("returnTrace", False)
        refine_prompt = body.get("refine_prompt", False)
        rerank = body.get("rerank", False)
        
        # Validate refine_prompt
        if not isinstance(refine_prompt, bool):
            refine_prompt = False
        
        # Validate rerank
        if not isinstance(rerank, bool):
            rerank = False
        
        # Validate question
        if not question or not question.strip():
            return JsonResponse(
                {"error": "Question is required", "code": "VALIDATION_ERROR"},
                status=400
            )
        
        question = question.strip()
        if len(question) > MAX_QUESTION_LENGTH:
            return JsonResponse(
                {
                    "error": f"Question too long. Maximum {MAX_QUESTION_LENGTH} characters.",
                    "code": "VALIDATION_ERROR"
                },
                status=400
            )
        
        # Validate mode (only "agent" supported for now)
        if mode != "agent":
            return JsonResponse(
                {"error": "Invalid mode. Only 'agent' is supported.", "code": "VALIDATION_ERROR"},
                status=400
            )
        
        # Get user ID
        user_id = request.user_claims.sub
        if not user_id:
            return JsonResponse({"error": "Invalid token: missing sub"}, status=401)
        
        try:
            # Query rewriting (optional step)
            rewritten_query = None
            retrieval_question = question  # Default to original
            
            if refine_prompt:
                logger.info("Agent: Query refinement enabled, calling rewriter")
                rewrite_result = rewrite_query(question)
                if rewrite_result:
                    rewritten_query = rewrite_result.rewritten_query
                    retrieval_question = rewritten_query
                    logger.info(f"Agent: Query refined: '{retrieval_question[:100]}...'")
                else:
                    logger.info("Agent: Query refinement failed, using original question")
            
            # Execute agent (use retrieval_question for search, original for final answer)
            result = run_agent(retrieval_question, user_id, rerank=rerank)
            
            # Audit log
            log_audit_from_request(
                request,
                'agent.run',
                metadata={
                    'question_length': len(question),
                    'tool_calls': sum(1 for t in result.trace if t.type.value == 'tool_call'),
                    'trace_length': len(result.trace),
                }
            )
            
            # Build response
            response_data = result.to_dict(include_trace=return_trace)
            if rewritten_query:
                response_data["rewritten_query"] = rewritten_query
            
            return JsonResponse(response_data)
            
        except AgentError as e:
            logger.error(f"Agent error: {e}")
            return JsonResponse(
                {"error": str(e), "code": "AGENT_ERROR"},
                status=400
            )
        except Exception as e:
            logger.exception(f"Unexpected error in agent: {e}")
            return JsonResponse(
                {"error": "Internal server error", "code": "INTERNAL_ERROR"},
                status=500
            )


@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(auth_required, name='dispatch')
@method_decorator(rate_limited(check_ask_rate_limit), name='dispatch')
class AgentStreamView(View):
    """
    POST /api/agent/stream
    
    Execute bounded agent loop with Server-Sent Events for real-time updates.
    
    Request body:
        {
            "question": "What are the key findings?"
        }
    
    Response: SSE stream with events:
        event: trace
        data: {"type": "plan", "steps": [...]}
        
        event: trace
        data: {"type": "tool_call", "tool": "search_docs", ...}
        
        event: complete
        data: {"answer": "...", "citations": [...], "trace": [...]}
    """
    
    def post(self, request):
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        
        question = body.get("question", "")
        refine_prompt = body.get("refine_prompt", False)
        rerank = body.get("rerank", False)
        
        # Validate refine_prompt
        if not isinstance(refine_prompt, bool):
            refine_prompt = False
        
        # Validate rerank
        if not isinstance(rerank, bool):
            rerank = False
        
        # Validate question
        if not question or not question.strip():
            return JsonResponse(
                {"error": "Question is required", "code": "VALIDATION_ERROR"},
                status=400
            )
        
        question = question.strip()
        if len(question) > MAX_QUESTION_LENGTH:
            return JsonResponse(
                {
                    "error": f"Question too long. Maximum {MAX_QUESTION_LENGTH} characters.",
                    "code": "VALIDATION_ERROR"
                },
                status=400
            )
        
        # Get user ID
        user_id = request.user_claims.sub
        if not user_id:
            return JsonResponse({"error": "Invalid token: missing sub"}, status=401)
        
        def event_stream():
            """Generate SSE events from agent execution."""
            tool_calls_count = 0
            final_trace = []
            
            # Query rewriting (optional step)
            rewritten_query = None
            retrieval_question = question  # Default to original
            
            if refine_prompt:
                logger.info("Agent stream: Query refinement enabled, calling rewriter")
                rewrite_result = rewrite_query(question)
                if rewrite_result:
                    rewritten_query = rewrite_result.rewritten_query
                    retrieval_question = rewritten_query
                    logger.info(f"Agent stream: Query refined: '{retrieval_question[:100]}...'")
                else:
                    logger.info("Agent stream: Query refinement failed, using original question")
            
            try:
                for item in run_agent_streaming(retrieval_question, user_id, rerank=rerank):
                    if isinstance(item, TraceEntry):
                        # Stream trace entry
                        final_trace.append(item)
                        if item.type.value == 'tool_call':
                            tool_calls_count += 1
                        
                        event_data = json.dumps(item.to_dict())
                        yield f"event: trace\ndata: {event_data}\n\n"
                        
                    elif isinstance(item, AgentResult):
                        # Final result
                        result_data = item.to_dict(include_trace=True)
                        
                        # Add rewritten_query if refinement was used
                        if rewritten_query:
                            result_data["rewritten_query"] = rewritten_query
                        
                        # Audit log
                        log_audit_from_request(
                            request,
                            'agent.stream',
                            metadata={
                                'question_length': len(question),
                                'tool_calls': tool_calls_count,
                                'trace_length': len(final_trace),
                            }
                        )
                        
                        event_data = json.dumps(result_data)
                        yield f"event: complete\ndata: {event_data}\n\n"
                        
            except AgentError as e:
                error_data = json.dumps({"error": str(e), "code": "AGENT_ERROR"})
                yield f"event: error\ndata: {error_data}\n\n"
                
            except Exception as e:
                logger.exception(f"Streaming agent error: {e}")
                error_data = json.dumps({"error": "Internal server error", "code": "INTERNAL_ERROR"})
                yield f"event: error\ndata: {error_data}\n\n"
        
        response = StreamingHttpResponse(
            event_stream(),
            content_type='text/event-stream'
        )
        response['Cache-Control'] = 'no-cache'
        response['X-Accel-Buffering'] = 'no'  # Disable nginx buffering
        return response
