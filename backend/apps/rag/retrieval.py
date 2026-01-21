"""
Retrieval service for RAG queries.

Performs user-scoped vector similarity search to find
relevant document chunks for a given query.
"""
import logging
from dataclasses import dataclass
from typing import List, Optional

from django.db import connection

from apps.docs.models import Document, DocumentStatus
from apps.indexing.models import DocumentChunk

logger = logging.getLogger(__name__)

# Default number of chunks to retrieve
DEFAULT_TOP_K = 5

# Maximum snippet length for citations
SNIPPET_MAX_LENGTH = 350


@dataclass
class Citation:
    """A citation referencing a specific chunk in a document."""
    doc_id: str
    chunk_id: str
    chunk_index: int
    snippet: str  # Truncated text for UI display
    score: float  # Similarity score (lower = more similar for cosine distance)
    document_title: str
    text: str = ""  # Full chunk text for LLM context
    
    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict (excludes full text for response size)."""
        return {
            "docId": self.doc_id,
            "chunkId": self.chunk_id,
            "chunkIndex": self.chunk_index,
            "snippet": self.snippet,
            "score": round(self.score, 4),
            "documentTitle": self.document_title,
        }


@dataclass
class RetrievalResult:
    """Result of a retrieval query."""
    query: str
    citations: List[Citation]
    
    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict."""
        return {
            "query": self.query,
            "citations": [c.to_dict() for c in self.citations],
        }
    
    @property
    def context_text(self) -> str:
        """
        Get concatenated context text for LLM prompting.
        
        Formats each chunk with citation markers for reference.
        """
        parts = []
        for i, citation in enumerate(self.citations, 1):
            parts.append(f"[{i}] {citation.snippet}")
        return "\n\n".join(parts)


def create_snippet(text: str, max_length: int = SNIPPET_MAX_LENGTH) -> str:
    """
    Create a deterministic snippet from chunk text.
    
    - Takes first N characters
    - Adds ellipsis if truncated
    - Preserves word boundaries when possible
    
    Args:
        text: Full chunk text
        max_length: Maximum snippet length
        
    Returns:
        Truncated snippet string
    """
    if len(text) <= max_length:
        return text
    
    # Try to break at word boundary
    truncated = text[:max_length]
    last_space = truncated.rfind(' ')
    
    if last_space > max_length * 0.7:  # Only break at space if reasonable
        truncated = truncated[:last_space]
    
    return truncated.rstrip() + "…"


def retrieve_chunks(
    query_embedding: List[float],
    user_id: str,
    top_k: int = DEFAULT_TOP_K,
    min_score: Optional[float] = None,
) -> List[Citation]:
    """
    Retrieve top-k most similar chunks for a user's documents.
    
    Uses pgvector's cosine distance operator (<=>)to find nearest neighbors.
    Only searches documents owned by the user that are fully indexed.
    
    Args:
        query_embedding: Vector embedding of the user's query
        user_id: Keycloak user ID (sub claim) for scoping
        top_k: Number of chunks to retrieve
        min_score: Optional minimum similarity threshold (lower is better for cosine)
        
    Returns:
        List of Citation objects with snippets and scores
    """
    # Convert embedding to PostgreSQL array literal
    embedding_str = '[' + ','.join(str(x) for x in query_embedding) + ']'
    
    # Build the query with user scoping and status filter
    sql = """
        SELECT 
            c.id AS chunk_id,
            c.document_id,
            c.chunk_index,
            c.text,
            d.filename AS document_title,
            c.embedding <=> %s::vector AS distance
        FROM doc_chunks c
        INNER JOIN documents d ON c.document_id = d.id
        WHERE d.owner_user_id = %s
          AND d.status = %s
          AND c.embedding IS NOT NULL
        ORDER BY c.embedding <=> %s::vector
        LIMIT %s
    """
    
    params = [
        embedding_str,
        user_id,
        DocumentStatus.INDEXED,
        embedding_str,
        top_k,
    ]
    
    citations = []
    
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        
        for row in rows:
            chunk_id, doc_id, chunk_index, text, doc_title, distance = row
            
            # Apply minimum score filter if specified
            if min_score is not None and distance > min_score:
                continue
            
            citation = Citation(
                doc_id=str(doc_id),
                chunk_id=str(chunk_id),
                chunk_index=chunk_index,
                snippet=create_snippet(text),
                score=float(distance),
                document_title=doc_title,
                text=text,  # Full text for LLM context
            )
            citations.append(citation)
    
    logger.info(
        f"Retrieved {len(citations)} chunks for user {user_id} "
        f"(requested top_k={top_k})"
    )
    
    return citations


@dataclass
class RetrievalCandidate:
    """
    A retrieval candidate with full text for reranking.
    
    Similar to Citation but includes full chunk text.
    """
    doc_id: str
    chunk_id: str
    chunk_index: int
    text: str  # Full chunk text
    snippet: str
    vector_score: float
    document_title: str
    
    def to_citation(self) -> Citation:
        """Convert to Citation (drops full text)."""
        return Citation(
            doc_id=self.doc_id,
            chunk_id=self.chunk_id,
            chunk_index=self.chunk_index,
            snippet=self.snippet,
            score=self.vector_score,
            document_title=self.document_title,
        )


def retrieve_chunks_for_reranking(
    query_embedding: List[float],
    user_id: str,
    top_k: int = DEFAULT_TOP_K,
) -> List[RetrievalCandidate]:
    """
    Retrieve top-k candidates for reranking (includes full text).
    
    Similar to retrieve_chunks but returns full chunk text for reranker.
    
    Args:
        query_embedding: Vector embedding of the user's query
        user_id: Keycloak user ID (sub claim) for scoping
        top_k: Number of chunks to retrieve
        
    Returns:
        List of RetrievalCandidate objects with full text
    """
    # Convert embedding to PostgreSQL array literal
    embedding_str = '[' + ','.join(str(x) for x in query_embedding) + ']'
    
    # Build the query with user scoping and status filter
    sql = """
        SELECT 
            c.id AS chunk_id,
            c.document_id,
            c.chunk_index,
            c.text,
            d.filename AS document_title,
            c.embedding <=> %s::vector AS distance
        FROM doc_chunks c
        INNER JOIN documents d ON c.document_id = d.id
        WHERE d.owner_user_id = %s
          AND d.status = %s
          AND c.embedding IS NOT NULL
        ORDER BY c.embedding <=> %s::vector
        LIMIT %s
    """
    
    params = [
        embedding_str,
        user_id,
        DocumentStatus.INDEXED,
        embedding_str,
        top_k,
    ]
    
    candidates = []
    
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        
        for row in rows:
            chunk_id, doc_id, chunk_index, text, doc_title, distance = row
            
            candidate = RetrievalCandidate(
                doc_id=str(doc_id),
                chunk_id=str(chunk_id),
                chunk_index=chunk_index,
                text=text,  # Full text for reranking
                snippet=create_snippet(text),
                vector_score=float(distance),
                document_title=doc_title,
            )
            candidates.append(candidate)
    
    logger.info(
        f"Retrieved {len(candidates)} candidates for reranking, user {user_id}"
    )
    
    return candidates


def retrieve_for_query(
    query: str,
    query_embedding: List[float],
    user_id: str,
    top_k: int = DEFAULT_TOP_K,
) -> RetrievalResult:
    """
    Full retrieval pipeline for a user query.
    
    Args:
        query: The user's question (normalized)
        query_embedding: Vector embedding of the query
        user_id: Keycloak user ID for scoping
        top_k: Number of chunks to retrieve
        
    Returns:
        RetrievalResult with citations
    """
    citations = retrieve_chunks(
        query_embedding=query_embedding,
        user_id=user_id,
        top_k=top_k,
    )
    
    return RetrievalResult(
        query=query,
        citations=citations,
    )
