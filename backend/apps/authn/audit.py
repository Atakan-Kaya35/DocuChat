"""
Audit logging for security and compliance.

Provides structured JSON logging for key events without exposing sensitive content.
See OPERATIONS.md for schema and event types.
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from functools import wraps

# Dedicated audit logger
audit_logger = logging.getLogger('audit')


class AuditEvent:
    """Standard audit event types."""
    # Auth events
    AUTH_TOKEN_VALIDATED = 'auth.token_validated'
    AUTH_TOKEN_REJECTED = 'auth.token_rejected'
    
    # Document events
    DOCUMENT_UPLOADED = 'document.uploaded'
    DOCUMENT_DUPLICATE = 'document.duplicate'
    DOCUMENT_DELETED = 'document.deleted'
    
    # Indexing events
    INDEXING_STARTED = 'indexing.started'
    INDEXING_COMPLETED = 'indexing.completed'
    INDEXING_FAILED = 'indexing.failed'
    
    # RAG events
    RAG_QUERY = 'rag.query'
    
    # Rate limiting events
    RATELIMIT_EXCEEDED = 'ratelimit.exceeded'


def get_client_ip(request) -> str:
    """Extract client IP from request, handling proxies."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        # First IP in the chain is the client
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', 'unknown')


def get_request_id(request) -> str:
    """Get or generate a request ID for correlation."""
    request_id = getattr(request, 'request_id', None)
    if not request_id:
        request_id = request.META.get('HTTP_X_REQUEST_ID')
    if not request_id:
        request_id = str(uuid.uuid4())[:8]
    return request_id


def log_audit(
    event_type: str,
    user_id: Optional[str] = None,
    request_id: Optional[str] = None,
    client_ip: Optional[str] = None,
    outcome: str = 'success',
    metadata: Optional[Dict[str, Any]] = None
):
    """
    Log a structured audit event.
    
    Args:
        event_type: One of AuditEvent constants
        user_id: Keycloak subject ID (from JWT)
        request_id: Correlation ID for request tracing
        client_ip: Client IP address
        outcome: 'success' or 'failure'
        metadata: Event-specific data (no PII/secrets)
    """
    event = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'event_type': event_type,
        'user_id': user_id,
        'request_id': request_id,
        'client_ip': client_ip,
        'outcome': outcome,
        'metadata': metadata or {}
    }
    
    # Log as structured JSON
    audit_logger.info(json.dumps(event))


def log_audit_from_request(
    request,
    event_type: str,
    outcome: str = 'success',
    metadata: Optional[Dict[str, Any]] = None
):
    """
    Log an audit event with request context auto-populated.
    
    Args:
        request: Django HttpRequest
        event_type: One of AuditEvent constants
        outcome: 'success' or 'failure'
        metadata: Event-specific data
    """
    # Extract user ID from auth claims
    user_id = None
    if hasattr(request, 'user_claims') and request.user_claims:
        user_id = getattr(request.user_claims, 'sub', None)
    
    log_audit(
        event_type=event_type,
        user_id=user_id,
        request_id=get_request_id(request),
        client_ip=get_client_ip(request),
        outcome=outcome,
        metadata=metadata
    )


# Convenience functions for common events

def audit_document_uploaded(request, document_id: str, filename: str, size_bytes: int, content_hash: str):
    """Log successful document upload."""
    log_audit_from_request(
        request,
        AuditEvent.DOCUMENT_UPLOADED,
        metadata={
            'document_id': document_id,
            'filename': filename,
            'size_bytes': size_bytes,
            'content_hash': content_hash[:16] + '...',  # Truncate for brevity
        }
    )


def audit_document_duplicate(request, document_id: str, existing_id: str):
    """Log duplicate document upload detected."""
    log_audit_from_request(
        request,
        AuditEvent.DOCUMENT_DUPLICATE,
        metadata={
            'document_id': document_id,
            'existing_id': existing_id,
        }
    )


def audit_rag_query(request, question_length: int, top_k: int, citation_count: int):
    """Log RAG query (without the actual question text)."""
    log_audit_from_request(
        request,
        AuditEvent.RAG_QUERY,
        metadata={
            'question_length': question_length,
            'top_k': top_k,
            'citation_count': citation_count,
        }
    )


def audit_indexing_started(job_id: str, document_id: str, user_id: str):
    """Log indexing job started."""
    log_audit(
        AuditEvent.INDEXING_STARTED,
        user_id=user_id,
        metadata={
            'job_id': job_id,
            'document_id': document_id,
        }
    )


def audit_indexing_completed(job_id: str, document_id: str, user_id: str, chunk_count: int):
    """Log indexing job completed."""
    log_audit(
        AuditEvent.INDEXING_COMPLETED,
        user_id=user_id,
        metadata={
            'job_id': job_id,
            'document_id': document_id,
            'chunk_count': chunk_count,
        }
    )


def audit_indexing_failed(job_id: str, document_id: str, user_id: str, error: str):
    """Log indexing job failed."""
    log_audit(
        AuditEvent.INDEXING_FAILED,
        user_id=user_id,
        outcome='failure',
        metadata={
            'job_id': job_id,
            'document_id': document_id,
            'error': error[:200],  # Truncate error message
        }
    )


def audit_ratelimit_exceeded(request, endpoint: str, limit: int, window: int):
    """Log rate limit exceeded."""
    log_audit_from_request(
        request,
        AuditEvent.RATELIMIT_EXCEEDED,
        outcome='failure',
        metadata={
            'endpoint': endpoint,
            'limit': limit,
            'window': window,
        }
    )


def audit_auth_validated(request, issuer: str, expires_at: int):
    """Log successful token validation."""
    log_audit_from_request(
        request,
        AuditEvent.AUTH_TOKEN_VALIDATED,
        metadata={
            'issuer': issuer,
            'expires_at': expires_at,
        }
    )


def audit_auth_rejected(request, reason: str):
    """Log failed token validation."""
    log_audit_from_request(
        request,
        AuditEvent.AUTH_TOKEN_REJECTED,
        outcome='failure',
        metadata={
            'reason': reason,
        }
    )
