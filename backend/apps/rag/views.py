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
from apps.rag.retrieval import retrieve_for_query, DEFAULT_TOP_K
from apps.rag.chat import generate_answer, ChatError, DEFAULT_TEMPERATURE, DEFAULT_MAX_TOKENS
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
        
        # Generate query embedding
        try:
            query_embedding = embed_query(question)
            logger.info(f"Query embedding generated: {len(query_embedding)} dimensions")
        except EmbeddingError as e:
            logger.error(f"Embedding failed: {e}")
            return JsonResponse(
                {"error": "Failed to process question"},
                status=503
            )
        
        # Retrieve relevant chunks
        retrieval_result = retrieve_for_query(
            query=question,
            query_embedding=query_embedding,
            user_id=user_id,
            top_k=top_k,
        )
        
        logger.info(f"Retrieved {len(retrieval_result.citations)} chunks for question")
        
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
        
        return JsonResponse(chat_response.to_dict())

