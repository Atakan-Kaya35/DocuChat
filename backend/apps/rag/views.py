"""
RAG API views.

Provides endpoints for:
- Query retrieval (get relevant chunks)
- Ask endpoint (full RAG with LLM)
"""
import logging
import json

from django.http import JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from apps.authn.middleware import auth_required
from apps.authn.ratelimit import rate_limited, check_ask_rate_limit
from apps.authn.audit import audit_rag_query
from apps.rag.embeddings import (
    normalize_query,
    embed_query,
    QueryValidationError,
    EmbeddingError,
)
from apps.rag.retrieval import (
    retrieve_for_query,
    retrieve_chunks,
    retrieve_chunks_for_reranking,
    RetrievalResult,
    RetrievalCandidate,
    Citation,
    DEFAULT_TOP_K,
)
from apps.rag.chat import generate_answer, ChatError, DEFAULT_TEMPERATURE, DEFAULT_MAX_TOKENS
from apps.rag.query_rewriter import rewrite_query
from apps.rag.reranker import (
    ChunkCandidate,
    rerank_candidates,
    is_reranker_enabled,
    get_rerank_top_k,
    get_rerank_keep_n,
)
from apps.indexing.retry import (
    retry_with_backoff,
    GENERATION_RETRY_CONFIG,
    RetryExhausted,
)

logger = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(auth_required, name='dispatch')
class RetrieveView(View):
    """
    POST /api/rag/retrieve
    
    Retrieve relevant document chunks for a query.
    Used for testing retrieval before full RAG.
    
    Request body:
        {
            "query": "What is the main topic?",
            "topK": 5  // optional, default 5
        }
    
    Response:
        {
            "query": "What is the main topic?",
            "citations": [
                {
                    "docId": "...",
                    "chunkId": "...",
                    "chunkIndex": 3,
                    "snippet": "...",
                    "score": 0.1234,
                    "documentTitle": "file.pdf"
                }
            ]
        }
    """
    
    def post(self, request):
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        
        # Extract and validate query
        raw_query = body.get("query", "")
        top_k = body.get("topK", DEFAULT_TOP_K)
        
        # Validate top_k
        if not isinstance(top_k, int) or top_k < 1 or top_k > 20:
            return JsonResponse(
                {"error": "topK must be an integer between 1 and 20"},
                status=400
            )
        
        # Normalize query
        try:
            query = normalize_query(raw_query)
        except QueryValidationError as e:
            return JsonResponse({"error": str(e)}, status=400)
        
        # Get user ID from JWT
        user_id = request.user_claims.sub
        if not user_id:
            return JsonResponse({"error": "Invalid token: missing sub"}, status=401)
        
        # Generate query embedding
        try:
            query_embedding = embed_query(query)
            logger.info(f"Query embedding generated: {len(query_embedding)} dimensions")
        except EmbeddingError as e:
            logger.error(f"Embedding failed: {e}")
            return JsonResponse(
                {"error": "Failed to process query"},
                status=503
            )
        
        # Retrieve relevant chunks
        result = retrieve_for_query(
            query=query,
            query_embedding=query_embedding,
            user_id=user_id,
            top_k=top_k,
        )
        
        return JsonResponse(result.to_dict())


@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(auth_required, name='dispatch')
class RewriteView(View):
    """
    POST /api/rag/rewrite
    
    Rewrite a query for better retrieval. Called by frontend to show
    refined query immediately before the full RAG call.
    
    Request body:
        {
            "question": "how do i fix the login bug"
        }
    
    Response:
        {
            "rewritten_query": "troubleshoot authentication login failure error",
            "original_query": "how do i fix the login bug"
        }
    """
    
    def post(self, request):
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        
        raw_question = body.get("question", "")
        
        # Normalize question
        try:
            question = normalize_query(raw_question)
        except QueryValidationError as e:
            return JsonResponse({"error": str(e)}, status=400)
        
        # Call query rewriter
        rewrite_result = rewrite_query(question)
        
        if rewrite_result:
            return JsonResponse({
                "rewritten_query": rewrite_result.rewritten_query,
                "original_query": question,
            })
        else:
            # Fallback - return original as both
            return JsonResponse({
                "rewritten_query": question,
                "original_query": question,
                "fallback": True,
            })


@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(auth_required, name='dispatch')
@method_decorator(rate_limited(check_ask_rate_limit), name='dispatch')
class AskView(View):
    """
    POST /api/rag/ask
    
    Full RAG pipeline: retrieve + LLM generation.
    
    Request body:
        {
            "question": "What is the main topic?",
            "topK": 5,           // optional, default 5
            "temperature": 0.2,  // optional, default 0.2
            "maxTokens": 500     // optional, default 500
        }
    
    Response:
        {
            "answer": "Based on the documents, the main topic is...[1]",
            "citations": [
                {
                    "docId": "...",
                    "chunkId": "...",
                    "chunkIndex": 3,
                    "snippet": "...",
                    "score": 0.1234,
                    "documentTitle": "file.pdf"
                }
            ],
            "model": "gemma:7b"
        }
    """
    
    def post(self, request):
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        
        # Extract parameters
        raw_question = body.get("question", "")
        top_k = body.get("topK", DEFAULT_TOP_K)
        temperature = body.get("temperature", DEFAULT_TEMPERATURE)
        max_tokens = body.get("maxTokens", DEFAULT_MAX_TOKENS)
        refine_prompt = body.get("refine_prompt", False)
        rerank = body.get("rerank", False)
        
        # Validate refine_prompt
        if not isinstance(refine_prompt, bool):
            refine_prompt = False
        
        # Validate rerank
        if not isinstance(rerank, bool):
            rerank = False
        
        # Validate top_k
        if not isinstance(top_k, int) or top_k < 1 or top_k > 20:
            return JsonResponse(
                {"error": "topK must be an integer between 1 and 20"},
                status=400
            )
        
        # Validate temperature
        if not isinstance(temperature, (int, float)) or temperature < 0 or temperature > 1:
            return JsonResponse(
                {"error": "temperature must be a number between 0 and 1"},
                status=400
            )
        
        # Validate max_tokens
        if not isinstance(max_tokens, int) or max_tokens < 50 or max_tokens > 2000:
            return JsonResponse(
                {"error": "maxTokens must be an integer between 50 and 2000"},
                status=400
            )
        
        # Normalize question
        try:
            question = normalize_query(raw_question)
        except QueryValidationError as e:
            return JsonResponse({"error": str(e)}, status=400)
        
        # Get user ID from JWT
        user_id = request.user_claims.sub
        if not user_id:
            return JsonResponse({"error": "Invalid token: missing sub"}, status=401)
        
        # Query rewriting (optional step)
        rewritten_query = None
        retrieval_query = question  # Default to original
        
        if refine_prompt:
            logger.info("Query refinement enabled, calling rewriter")
            rewrite_result = rewrite_query(question)
            if rewrite_result:
                rewritten_query = rewrite_result.rewritten_query
                retrieval_query = rewritten_query
                logger.info(f"Query refined: '{retrieval_query[:100]}...'")
            else:
                logger.info("Query refinement failed, using original question")
        
        # Generate query embedding (use retrieval_query for embedding)
        try:
            query_embedding = embed_query(retrieval_query)
            logger.info(f"Query embedding generated: {len(query_embedding)} dimensions")
        except EmbeddingError as e:
            logger.error(f"Embedding failed: {e}")
            return JsonResponse(
                {"error": "Failed to process question"},
                status=503
            )
        
        # Reranking logic
        rerank_used = False
        rerank_latency_ms = None
        
        # Check if reranking should be applied
        should_rerank = rerank and is_reranker_enabled()
        
        if should_rerank:
            # Retrieve more candidates for reranking
            rerank_top_k = get_rerank_top_k()
            rerank_keep_n = get_rerank_keep_n()
            
            try:
                # Get candidates with full text for reranking
                candidates = retrieve_chunks_for_reranking(
                    query_embedding=query_embedding,
                    user_id=user_id,
                    top_k=rerank_top_k,
                )
                
                if candidates:
                    # Convert to ChunkCandidate format for reranker
                    chunk_candidates = [
                        ChunkCandidate(
                            chunk_id=c.chunk_id,
                            doc_id=c.doc_id,
                            doc_title=c.document_title,
                            text=c.text,
                            snippet=c.snippet,
                            vector_score=c.vector_score,
                        )
                        for c in candidates
                    ]
                    
                    # Rerank candidates
                    reranked, rerank_latency_ms = rerank_candidates(
                        query=retrieval_query,
                        candidates=chunk_candidates,
                        top_n=rerank_keep_n,
                    )
                    
                    # Convert back to Citations
                    citations = [
                        Citation(
                            doc_id=c.doc_id,
                            chunk_id=c.chunk_id,
                            chunk_index=next(
                                (cand.chunk_index for cand in candidates if cand.chunk_id == c.chunk_id),
                                0
                            ),
                            snippet=c.snippet,
                            score=c.rerank_score if c.rerank_score is not None else c.vector_score,
                            document_title=c.doc_title,
                            text=c.text,  # Full text for LLM context
                        )
                        for c in reranked
                    ]
                    
                    retrieval_result = RetrievalResult(
                        query=retrieval_query,
                        citations=citations,
                    )
                    rerank_used = True
                    logger.info(
                        f"Reranked {len(candidates)} -> {len(citations)} chunks "
                        f"in {rerank_latency_ms:.0f}ms"
                    )
                else:
                    # No candidates, use empty result
                    retrieval_result = RetrievalResult(
                        query=retrieval_query,
                        citations=[],
                    )
                    logger.info("No candidates to rerank")
                    
            except Exception as e:
                # Reranking failed, fall back to standard retrieval
                logger.warning(f"Reranking failed, falling back to vector order: {e}")
                retrieval_result = retrieve_for_query(
                    query=retrieval_query,
                    query_embedding=query_embedding,
                    user_id=user_id,
                    top_k=top_k,
                )
        else:
            # Standard retrieval without reranking
            retrieval_result = retrieve_for_query(
                query=retrieval_query,
                query_embedding=query_embedding,
                user_id=user_id,
                top_k=top_k,
            )
        
        logger.info(
            f"Retrieved {len(retrieval_result.citations)} chunks for question "
            f"(rerank_used={rerank_used})"
        )
        
        # Generate answer with retry (handles no-context case internally)
        try:
            chat_response = retry_with_backoff(
                func=lambda: generate_answer(
                    question=question,
                    retrieval_result=retrieval_result,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ),
                config=GENERATION_RETRY_CONFIG,
                exceptions=(ChatError,),
                on_retry=lambda attempt, err, backoff: logger.warning(
                    f"LLM generation retry {attempt + 1}: {err}. Waiting {backoff:.1f}s"
                )
            )
        except RetryExhausted as e:
            logger.error(f"LLM generation failed after {e.attempts} attempts: {e.last_exception}")
            response = JsonResponse(
                {
                    "error": "LLM service temporarily unavailable",
                    "code": "LLM_UNAVAILABLE",
                    "retryable": True,
                },
                status=503
            )
            response["Retry-After"] = "30"
            return response
        except ChatError as e:
            # Non-retriable error
            logger.error(f"Chat generation failed (non-retriable): {e}")
            return JsonResponse(
                {"error": "Failed to generate answer"},
                status=503
            )
        
        # Audit successful RAG query (no content, just metadata)
        audit_rag_query(
            request,
            question_length=len(question),
            top_k=top_k,
            citation_count=len(chat_response.citations)
        )
        
        # Build response with optional rewritten_query for frontend display
        response_data = chat_response.to_dict()
        if rewritten_query:
            response_data["rewritten_query"] = rewritten_query
        
        # Add rerank debug metadata
        response_data["rerank_used"] = rerank_used
        if rerank_latency_ms is not None:
            response_data["rerank_latency_ms"] = round(rerank_latency_ms, 1)
        
        return JsonResponse(response_data)

