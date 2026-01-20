"""
Agent tools for DocuChat.

Provides bounded, secure tools for the agent to use:
- search_docs: Semantic search across user's documents
- open_citation: Retrieve full text of a specific chunk

See API.md and OPERATIONS.md for limits and contracts.
"""
import logging
from dataclasses import dataclass
from typing import List, Optional, Dict, Any

from django.conf import settings

from apps.indexing.models import DocumentChunk
from apps.docs.models import Document
from apps.rag.embeddings import embed_query, normalize_query, QueryValidationError, EmbeddingError
from apps.rag.retrieval import retrieve_for_query

logger = logging.getLogger(__name__)

# Hard limits (see OPERATIONS.md)
MAX_QUERY_LENGTH = 500
MAX_SEARCH_RESULTS = 5
MAX_CITATION_TEXT = 5000
SNIPPET_LENGTH = 200


@dataclass
class SearchResult:
    """Single result from search_docs tool."""
    doc_id: str
    chunk_id: str
    chunk_index: int
    snippet: str
    score: float
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'docId': self.doc_id,
            'chunkId': self.chunk_id,
            'chunkIndex': self.chunk_index,
            'snippet': self.snippet,
            'score': self.score,
        }


@dataclass
class SearchDocsOutput:
    """Output from search_docs tool."""
    results: List[SearchResult]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'results': [r.to_dict() for r in self.results]
        }
    
    def summary(self) -> str:
        """Generate a brief summary for trace."""
        if not self.results:
            return "No results found"
        return f"Found {len(self.results)} relevant chunks"


@dataclass
class OpenCitationOutput:
    """Output from open_citation tool."""
    doc_id: str
    chunk_id: str
    chunk_index: int
    text: str
    filename: str
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'docId': self.doc_id,
            'chunkId': self.chunk_id,
            'chunkIndex': self.chunk_index,
            'text': self.text,
            'filename': self.filename,
        }
    
    def summary(self) -> str:
        """Generate a brief summary for trace."""
        text_len = len(self.text)
        if text_len >= 1000:
            return f"Retrieved {text_len / 1000:.1f}KB from {self.filename}"
        return f"Retrieved {text_len} chars from {self.filename}"


class ToolError(Exception):
    """Base exception for tool errors."""
    pass


class ToolValidationError(ToolError):
    """Raised when tool input validation fails."""
    pass


class ToolAccessError(ToolError):
    """Raised when user doesn't have access to a resource."""
    pass


def search_docs(query: str, user_id: str) -> SearchDocsOutput:
    """
    Search user's documents for relevant content.
    
    Wraps Phase 3 retrieval with bounded query and results.
    
    Args:
        query: Search query (max 500 chars)
        user_id: Keycloak subject ID for ownership filtering
        
    Returns:
        SearchDocsOutput with up to 5 results
        
    Raises:
        ToolValidationError: If query is invalid
        ToolError: If search fails
    """
    # Validate and normalize query
    if not query or not query.strip():
        raise ToolValidationError("Query cannot be empty")
    
    query = query.strip()
    if len(query) > MAX_QUERY_LENGTH:
        logger.warning(f"Query truncated from {len(query)} to {MAX_QUERY_LENGTH} chars")
        query = query[:MAX_QUERY_LENGTH]
    
    try:
        # Normalize query (handles validation)
        normalized_query = normalize_query(query)
    except QueryValidationError as e:
        raise ToolValidationError(f"Invalid query: {e}")
    
    try:
        # Generate embedding
        query_embedding = embed_query(normalized_query)
        
        # Retrieve relevant chunks (scoped to user)
        retrieval_result = retrieve_for_query(
            query=normalized_query,
            query_embedding=query_embedding,
            user_id=user_id,
            top_k=MAX_SEARCH_RESULTS,
        )
        
        # Convert to SearchResult format
        results = []
        for citation in retrieval_result.citations:
            results.append(SearchResult(
                doc_id=citation.doc_id,
                chunk_id=citation.chunk_id,
                chunk_index=citation.chunk_index,
                snippet=citation.snippet[:SNIPPET_LENGTH] if citation.snippet else "",
                score=citation.score,
            ))
        
        logger.info(f"search_docs: query='{query[:50]}...' returned {len(results)} results")
        return SearchDocsOutput(results=results)
        
    except EmbeddingError as e:
        raise ToolError(f"Failed to embed query: {e}")
    except Exception as e:
        logger.exception(f"search_docs failed: {e}")
        raise ToolError(f"Search failed: {e}")


def open_citation(doc_id: str, chunk_id: str, user_id: str) -> OpenCitationOutput:
    """
    Retrieve full text of a specific citation.
    
    Performs ownership check to ensure user can access the document.
    
    Args:
        doc_id: Document UUID
        chunk_id: Chunk UUID
        user_id: Keycloak subject ID for ownership check
        
    Returns:
        OpenCitationOutput with chunk text and metadata
        
    Raises:
        ToolValidationError: If IDs are invalid
        ToolAccessError: If user doesn't own the document
        ToolError: If retrieval fails
    """
    # Validate inputs
    if not doc_id or not doc_id.strip():
        raise ToolValidationError("docId is required")
    if not chunk_id or not chunk_id.strip():
        raise ToolValidationError("chunkId is required")
    
    doc_id = doc_id.strip()
    chunk_id = chunk_id.strip()
    
    try:
        # Fetch chunk with document
        chunk = DocumentChunk.objects.select_related('document').get(id=chunk_id)
        
        # Verify document ID matches (defense in depth)
        if str(chunk.document.id) != doc_id:
            raise ToolValidationError("chunkId does not belong to specified docId")
        
        # Ownership check
        if chunk.document.owner_user_id != user_id:
            logger.warning(
                f"open_citation access denied: user {user_id} tried to access "
                f"doc {doc_id} owned by {chunk.document.owner_user_id}"
            )
            raise ToolAccessError("You do not have access to this document")
        
        # Get text (bounded)
        text = chunk.text or ""
        if len(text) > MAX_CITATION_TEXT:
            text = text[:MAX_CITATION_TEXT] + "\n\n[...text truncated...]"
        
        logger.info(f"open_citation: chunk {chunk_id} returned {len(text)} chars")
        
        return OpenCitationOutput(
            doc_id=doc_id,
            chunk_id=chunk_id,
            chunk_index=chunk.chunk_index,
            text=text,
            filename=chunk.document.filename,
        )
        
    except DocumentChunk.DoesNotExist:
        raise ToolValidationError(f"Chunk not found: {chunk_id}")
    except Document.DoesNotExist:
        raise ToolValidationError(f"Document not found: {doc_id}")
    except ToolError:
        raise
    except Exception as e:
        logger.exception(f"open_citation failed: {e}")
        raise ToolError(f"Failed to retrieve citation: {e}")


def open_citation_by_index(doc_id: str, chunk_index: int, user_id: str) -> OpenCitationOutput:
    """
    Retrieve full text of a citation by document ID and chunk index.
    
    Alternative to open_citation when chunk_id is not known.
    
    Args:
        doc_id: Document UUID
        chunk_index: Index of the chunk (0-based)
        user_id: Keycloak subject ID for ownership check
        
    Returns:
        OpenCitationOutput with chunk text and metadata
    """
    if not doc_id or not doc_id.strip():
        raise ToolValidationError("docId is required")
    if chunk_index < 0:
        raise ToolValidationError("chunkIndex must be non-negative")
    
    doc_id = doc_id.strip()
    
    try:
        # Fetch document first for ownership check
        document = Document.objects.get(id=doc_id)
        
        # Ownership check
        if document.owner_user_id != user_id:
            logger.warning(
                f"open_citation_by_index access denied: user {user_id} tried to access "
                f"doc {doc_id} owned by {document.owner_user_id}"
            )
            raise ToolAccessError("You do not have access to this document")
        
        # Fetch chunk
        chunk = DocumentChunk.objects.get(document=document, chunk_index=chunk_index)
        
        # Get text (bounded)
        text = chunk.text or ""
        if len(text) > MAX_CITATION_TEXT:
            text = text[:MAX_CITATION_TEXT] + "\n\n[...text truncated...]"
        
        logger.info(f"open_citation_by_index: doc {doc_id} chunk {chunk_index} returned {len(text)} chars")
        
        return OpenCitationOutput(
            doc_id=doc_id,
            chunk_id=str(chunk.id),
            chunk_index=chunk_index,
            text=text,
            filename=document.filename,
        )
        
    except Document.DoesNotExist:
        raise ToolValidationError(f"Document not found: {doc_id}")
    except DocumentChunk.DoesNotExist:
        raise ToolValidationError(f"Chunk {chunk_index} not found in document {doc_id}")
    except ToolError:
        raise
    except Exception as e:
        logger.exception(f"open_citation_by_index failed: {e}")
        raise ToolError(f"Failed to retrieve citation: {e}")
