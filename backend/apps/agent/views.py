"""
Agent API views.

Provides the bounded agent endpoint for multi-step question answering.
"""
import json
import logging

from django.http import JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from apps.authn.middleware import auth_required
from apps.authn.ratelimit import rate_limited, check_ask_rate_limit
from apps.authn.audit import log_audit_from_request, AuditEvent
from apps.agent.executor import run_agent, AgentError, MAX_QUESTION_LENGTH

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
            # Execute agent
            result = run_agent(question, user_id)
            
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
