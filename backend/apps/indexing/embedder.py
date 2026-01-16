"""
Embedding generation using Ollama.

Calls the Ollama API to generate vector embeddings for text chunks.
Uses the nomic-embed-text model which produces 768-dimensional vectors.
"""
import logging
from typing import List, Optional
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Embedding model configuration
EMBEDDING_MODEL = "nomic-embed-text"
EMBEDDING_DIMENSIONS = 768


class EmbeddingError(Exception):
    """Raised when embedding generation fails."""
    pass


def get_ollama_url() -> str:
    """Get the Ollama base URL from settings."""
    return getattr(settings, 'OLLAMA_BASE_URL', 'http://ollama:11434')


def generate_embedding(text: str, model: str = EMBEDDING_MODEL) -> List[float]:
    """
    Generate an embedding vector for a single text.
    
    Args:
        text: The text to embed
        model: The Ollama model to use (default: nomic-embed-text)
        
    Returns:
        List of floats representing the embedding vector
        
    Raises:
        EmbeddingError: If the API call fails
    """
    if not text or not text.strip():
        raise EmbeddingError("Cannot generate embedding for empty text")
    
    url = f"{get_ollama_url()}/api/embeddings"
    
    try:
        response = requests.post(
            url,
            json={
                "model": model,
                "prompt": text
            },
            timeout=60  # Embeddings should be quick
        )
        
        if response.status_code != 200:
            error_detail = response.text[:500] if response.text else "No details"
            raise EmbeddingError(
                f"Ollama API returned {response.status_code}: {error_detail}"
            )
        
        data = response.json()
        embedding = data.get("embedding")
        
        if not embedding:
            raise EmbeddingError("No embedding in response")
        
        if len(embedding) != EMBEDDING_DIMENSIONS:
            logger.warning(
                f"Expected {EMBEDDING_DIMENSIONS} dimensions, got {len(embedding)}"
            )
        
        return embedding
        
    except requests.exceptions.Timeout:
        raise EmbeddingError("Ollama API timed out")
    except requests.exceptions.ConnectionError:
        raise EmbeddingError(f"Cannot connect to Ollama at {get_ollama_url()}")
    except requests.exceptions.RequestException as e:
        raise EmbeddingError(f"Request failed: {e}")


def generate_embeddings_batch(
    texts: List[str],
    model: str = EMBEDDING_MODEL,
    on_progress: Optional[callable] = None
) -> List[List[float]]:
    """
    Generate embeddings for multiple texts.
    
    Note: Ollama doesn't have a true batch API, so we process sequentially.
    This is fine for MVP but could be optimized with async requests.
    
    Args:
        texts: List of texts to embed
        model: The Ollama model to use
        on_progress: Optional callback(current, total) for progress updates
        
    Returns:
        List of embedding vectors (same order as input)
        
    Raises:
        EmbeddingError: If any embedding fails
    """
    embeddings = []
    total = len(texts)
    
    for i, text in enumerate(texts):
        try:
            embedding = generate_embedding(text, model)
            embeddings.append(embedding)
            
            if on_progress:
                on_progress(i + 1, total)
                
        except EmbeddingError as e:
            logger.error(f"Failed to embed text {i+1}/{total}: {e}")
            raise
    
    logger.info(f"Generated {len(embeddings)} embeddings")
    return embeddings


def test_ollama_connection() -> bool:
    """
    Test if Ollama is reachable and the embedding model is available.
    
    Returns:
        True if connection is successful, False otherwise
    """
    try:
        # Check if Ollama is running
        url = f"{get_ollama_url()}/api/tags"
        response = requests.get(url, timeout=5)
        
        if response.status_code != 200:
            logger.error(f"Ollama returned {response.status_code}")
            return False
        
        # Check if our model is available
        data = response.json()
        models = [m.get("name", "") for m in data.get("models", [])]
        
        # Model names might include tags like :latest
        model_available = any(
            EMBEDDING_MODEL in m or m.startswith(EMBEDDING_MODEL)
            for m in models
        )
        
        if not model_available:
            logger.warning(
                f"Model {EMBEDDING_MODEL} not found. Available: {models}"
            )
            return False
        
        logger.info(f"Ollama connection OK, model {EMBEDDING_MODEL} available")
        return True
        
    except Exception as e:
        logger.error(f"Ollama connection test failed: {e}")
        return False
