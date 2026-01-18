"""
Embedding service for RAG queries.

Uses Ollama to generate embeddings for user questions,
matching the same model used for document chunks.
"""
import logging
import re
from typing import List, Optional

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

# Must match the dimension in DocumentChunk.embedding
EMBEDDING_DIMENSION = 768


class EmbeddingError(Exception):
    """Raised when embedding generation fails."""
    pass


class QueryValidationError(Exception):
    """Raised when query validation fails."""
    pass


def normalize_query(query: str) -> str:
    """
    Normalize a user query for embedding.
    
    - Strip leading/trailing whitespace
    - Collapse multiple whitespace to single space
    - Raise if empty
    
    Args:
        query: Raw user question
        
    Returns:
        Normalized query string
        
    Raises:
        QueryValidationError: If query is empty after normalization
    """
    if not query:
        raise QueryValidationError("Query cannot be empty")
    
    # Strip and collapse whitespace
    normalized = re.sub(r'\s+', ' ', query.strip())
    
    if not normalized:
        raise QueryValidationError("Query cannot be empty")
    
    if len(normalized) > 2000:
        raise QueryValidationError("Query too long (max 2000 characters)")
    
    return normalized


def embed_query(query: str) -> List[float]:
    """
    Generate embedding vector for a user query.
    
    Uses Ollama's embedding endpoint with the same model
    used for document chunks (nomic-embed-text).
    
    Args:
        query: Normalized user question
        
    Returns:
        Embedding vector as list of floats
        
    Raises:
        EmbeddingError: If Ollama call fails
    """
    ollama_url = getattr(settings, 'OLLAMA_BASE_URL', 'http://ollama:11434')
    embedding_model = getattr(settings, 'OLLAMA_EMBED_MODEL', 'nomic-embed-text')
    embed_timeout = getattr(settings, 'OLLAMA_EMBED_TIMEOUT', 120)  # 2 min default
    
    try:
        with httpx.Client(timeout=float(embed_timeout)) as client:
            # Use /api/embeddings (same as indexing pipeline)
            response = client.post(
                f"{ollama_url}/api/embeddings",
                json={
                    "model": embedding_model,
                    "prompt": query
                }
            )
            response.raise_for_status()
            data = response.json()
            
            # Ollama /api/embeddings returns {"embedding": [...]}
            embedding = data.get("embedding")
            if not embedding:
                raise EmbeddingError("Ollama returned empty embedding")
            
            # Validate dimension
            if len(embedding) != EMBEDDING_DIMENSION:
                logger.warning(
                    f"Embedding dimension mismatch: expected {EMBEDDING_DIMENSION}, "
                    f"got {len(embedding)}"
                )
            
            logger.debug(f"Generated query embedding with {len(embedding)} dimensions")
            return embedding
            
    except httpx.HTTPStatusError as e:
        logger.error(f"Ollama embedding request failed: {e}")
        raise EmbeddingError(f"Embedding service error: {e.response.status_code}")
    except httpx.RequestError as e:
        logger.error(f"Ollama connection error: {e}")
        raise EmbeddingError("Could not connect to embedding service")
    except (KeyError, IndexError, TypeError) as e:
        logger.error(f"Unexpected Ollama response format: {e}")
        raise EmbeddingError("Invalid response from embedding service")


def embed_query_safe(query: str) -> Optional[List[float]]:
    """
    Safe wrapper for embed_query that returns None on failure.
    
    Useful for graceful degradation when embeddings fail.
    """
    try:
        normalized = normalize_query(query)
        return embed_query(normalized)
    except (QueryValidationError, EmbeddingError) as e:
        logger.warning(f"Query embedding failed: {e}")
        return None
