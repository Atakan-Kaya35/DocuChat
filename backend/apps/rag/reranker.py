"""
Cross-Encoder Reranker module for RAG.

Reranks retrieved chunks using a cross-encoder model to improve relevance.
This is an optional feature that can be toggled per request.

Key features:
- Uses cross-encoder/ms-marco-MiniLM-L-6-v2 model
- Lazy model loading (loads on first use)
- Automatic GPU/CPU detection
- Silent fallback on any error
- Batch scoring for efficiency
"""
import logging
import time
from dataclasses import dataclass
from typing import List, Optional

from django.conf import settings

logger = logging.getLogger(__name__)

# Cross-encoder model name
CROSS_ENCODER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

# Maximum chunk text length to send to cross-encoder (characters)
# ~1500 chars is roughly 256-512 tokens for this model
MAX_CHUNK_TEXT_LENGTH = 1500


@dataclass
class ChunkCandidate:
    """A chunk candidate for reranking."""
    chunk_id: str
    doc_id: str
    doc_title: str
    text: str
    snippet: str
    vector_score: float
    # Set after reranking
    rerank_score: Optional[float] = None

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict."""
        result = {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "doc_title": self.doc_title,
            "text": self.text,
            "snippet": self.snippet,
            "vector_score": round(self.vector_score, 4),
        }
        if self.rerank_score is not None:
            result["rerank_score"] = round(self.rerank_score, 4)
        return result


class CrossEncoderReranker:
    """
    Reranker using a cross-encoder model for semantic relevance scoring.
    
    The cross-encoder scores (query, chunk_text) pairs directly,
    providing more accurate relevance than vector similarity alone.
    """
    
    _instance: Optional['CrossEncoderReranker'] = None
    _model = None
    _device: Optional[str] = None
    
    def __new__(cls):
        """Singleton pattern for model caching."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def _load_model(self):
        """
        Lazy load the cross-encoder model.
        
        Uses GPU if available, otherwise CPU.
        Model is cached in HuggingFace cache directory.
        """
        if self._model is not None:
            return
        
        try:
            import torch
            from sentence_transformers import CrossEncoder
            
            # Detect device
            if torch.cuda.is_available():
                self._device = "cuda"
                logger.info("CrossEncoder: Using CUDA GPU")
            else:
                self._device = "cpu"
                logger.info("CrossEncoder: Using CPU")
            
            # Load model (downloads on first use, cached thereafter)
            logger.info(f"Loading cross-encoder model: {CROSS_ENCODER_MODEL}")
            start_time = time.time()
            
            self._model = CrossEncoder(
                CROSS_ENCODER_MODEL,
                max_length=512,  # Model max sequence length
                device=self._device,
            )
            
            load_time_ms = (time.time() - start_time) * 1000
            logger.info(f"Cross-encoder model loaded in {load_time_ms:.0f}ms")
            
        except ImportError as e:
            logger.error(f"Failed to import cross-encoder dependencies: {e}")
            raise RuntimeError(
                "sentence-transformers or torch not installed. "
                "Install with: pip install sentence-transformers torch"
            )
        except Exception as e:
            logger.error(f"Failed to load cross-encoder model: {e}")
            raise
    
    def _truncate_text(self, text: str, max_length: int = MAX_CHUNK_TEXT_LENGTH) -> str:
        """
        Truncate text to avoid excessive token usage.
        
        Args:
            text: Full chunk text
            max_length: Maximum character length
            
        Returns:
            Truncated text
        """
        if len(text) <= max_length:
            return text
        
        # Truncate at word boundary if possible
        truncated = text[:max_length]
        last_space = truncated.rfind(' ')
        
        if last_space > max_length * 0.7:
            truncated = truncated[:last_space]
        
        return truncated.rstrip()
    
    def rerank(
        self,
        query: str,
        candidates: List[ChunkCandidate],
        top_n: Optional[int] = None,
    ) -> List[ChunkCandidate]:
        """
        Rerank candidates using cross-encoder scores.
        
        Args:
            query: The search query (original or rewritten)
            candidates: List of chunk candidates from vector retrieval
            top_n: Number of top results to return (default: all)
            
        Returns:
            Candidates sorted by rerank score (descending)
        """
        if not candidates:
            return candidates
        
        # Load model if not already loaded
        self._load_model()
        
        if self._model is None:
            raise RuntimeError("Cross-encoder model not loaded")
        
        start_time = time.time()
        
        # Prepare query-text pairs for batch scoring
        pairs = [
            (query, self._truncate_text(candidate.text))
            for candidate in candidates
        ]
        
        # Score all pairs in a single batch
        scores = self._model.predict(pairs, show_progress_bar=False)
        
        # Attach scores to candidates
        for candidate, score in zip(candidates, scores):
            candidate.rerank_score = float(score)
        
        # Sort by rerank score (descending - higher is better)
        sorted_candidates = sorted(
            candidates,
            key=lambda c: c.rerank_score if c.rerank_score is not None else float('-inf'),
            reverse=True,
        )
        
        rerank_time_ms = (time.time() - start_time) * 1000
        logger.debug(
            f"Reranked {len(candidates)} candidates in {rerank_time_ms:.0f}ms"
        )
        
        # Return top_n if specified
        if top_n is not None:
            return sorted_candidates[:top_n]
        
        return sorted_candidates


# Global reranker instance (lazy loaded)
_reranker: Optional[CrossEncoderReranker] = None


def get_reranker() -> CrossEncoderReranker:
    """Get the global reranker instance."""
    global _reranker
    if _reranker is None:
        _reranker = CrossEncoderReranker()
    return _reranker


def rerank_candidates(
    query: str,
    candidates: List[ChunkCandidate],
    top_n: Optional[int] = None,
) -> tuple[List[ChunkCandidate], float]:
    """
    Convenience function to rerank candidates with timing.
    
    Args:
        query: The search query
        candidates: List of chunk candidates
        top_n: Number of top results to return
        
    Returns:
        Tuple of (reranked candidates, latency in ms)
        
    Raises:
        Exception: If reranking fails
    """
    start_time = time.time()
    
    reranker = get_reranker()
    reranked = reranker.rerank(query, candidates, top_n)
    
    latency_ms = (time.time() - start_time) * 1000
    
    return reranked, latency_ms


def is_reranker_enabled() -> bool:
    """Check if reranker is enabled at server level."""
    return getattr(settings, 'ENABLE_RERANKER', False)


def get_rerank_top_k() -> int:
    """Get the number of candidates to retrieve for reranking."""
    return getattr(settings, 'RERANK_TOP_K', 20)


def get_rerank_keep_n() -> int:
    """Get the number of candidates to keep after reranking."""
    return getattr(settings, 'RERANK_KEEP_N', 8)
