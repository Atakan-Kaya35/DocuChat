"""
Event publisher for indexing progress.

Publishes events to Django Channels layer for broadcast to WebSocket clients.
"""
import logging
from typing import Optional
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from apps.indexing.events import IndexProgressEvent, EventType

logger = logging.getLogger(__name__)


def publish_progress(
    document_id: str,
    job_id: str,
    user_id: str,
    stage: str,
    progress: int,
    message: Optional[str] = None
) -> None:
    """
    Publish a progress event to the user's WebSocket channel.
    
    Args:
        document_id: UUID of the document
        job_id: UUID of the indexing job
        user_id: User ID from JWT (sub claim)
        stage: Current processing stage
        progress: Progress percentage (0-100)
        message: Optional human-readable message
    """
    event = IndexProgressEvent.progress(
        document_id=document_id,
        job_id=job_id,
        user_id=user_id,
        stage=stage,
        progress=progress,
        message=message
    )
    
    _send_to_user(user_id, "index_progress", event)


def publish_complete(
    document_id: str,
    job_id: str,
    user_id: str
) -> None:
    """Publish a completion event."""
    event = IndexProgressEvent.complete(
        document_id=document_id,
        job_id=job_id,
        user_id=user_id
    )
    
    _send_to_user(user_id, "index_complete", event)


def publish_failed(
    document_id: str,
    job_id: str,
    user_id: str,
    error_message: str
) -> None:
    """Publish a failure event."""
    event = IndexProgressEvent.failed(
        document_id=document_id,
        job_id=job_id,
        user_id=user_id,
        error_message=error_message
    )
    
    _send_to_user(user_id, "index_failed", event)


def _send_to_user(user_id: str, event_type: str, event: IndexProgressEvent) -> None:
    """
    Send an event to all WebSocket connections for a user.
    
    Uses Django Channels group send.
    """
    try:
        channel_layer = get_channel_layer()
        
        if channel_layer is None:
            logger.warning("Channel layer not available, cannot send event")
            return
        
        group_name = f"user_{user_id}"
        
        # Send to the group (all of this user's WebSocket connections)
        async_to_sync(channel_layer.group_send)(
            group_name,
            {
                "type": event_type,
                "data": event.to_dict()
            }
        )
        
        logger.debug(f"Published {event_type} to {group_name}: stage={event.stage}, progress={event.progress}")
        
    except Exception as e:
        # Don't fail the job if event publishing fails
        logger.warning(f"Failed to publish event: {e}")
