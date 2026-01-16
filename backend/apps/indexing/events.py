"""
WebSocket Progress Event Schema

Event contract for real-time indexing progress updates.

Events are published to Redis pub/sub and broadcast to connected WebSocket clients.
Client subscribes to their own documents (filtered by user ID from JWT).
"""
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Optional
import json


class EventType(str, Enum):
    """Types of WebSocket events."""
    INDEX_PROGRESS = "index_progress"
    INDEX_COMPLETE = "index_complete"
    INDEX_FAILED = "index_failed"


class ProgressStage(str, Enum):
    """
    Stages of the indexing pipeline.
    
    Order: EXTRACT -> CHUNK -> EMBED -> STORE -> COMPLETE
    Or FAILED at any point.
    """
    RECEIVED = "RECEIVED"  # Job received, not yet started
    EXTRACT = "EXTRACT"    # Extracting text from document
    CHUNK = "CHUNK"        # Chunking text into segments
    EMBED = "EMBED"        # Generating embeddings
    STORE = "STORE"        # Storing chunks with vectors
    COMPLETE = "COMPLETE"  # All done
    FAILED = "FAILED"      # Error occurred


@dataclass
class IndexProgressEvent:
    """
    Event sent to clients when indexing progress changes.
    
    Schema:
    {
        "type": "index_progress",
        "documentId": "uuid-string",
        "jobId": "uuid-string",
        "userId": "user-id-from-jwt",
        "stage": "EXTRACT|CHUNK|EMBED|STORE|COMPLETE|FAILED",
        "progress": 0-100,
        "message": "optional human-readable message"
    }
    """
    type: str
    documentId: str
    jobId: str
    userId: str
    stage: str
    progress: int
    message: Optional[str] = None
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        data = asdict(self)
        # Remove None values
        return {k: v for k, v in data.items() if v is not None}
    
    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict())
    
    @classmethod
    def progress(
        cls,
        document_id: str,
        job_id: str,
        user_id: str,
        stage: str,
        progress: int,
        message: Optional[str] = None
    ) -> 'IndexProgressEvent':
        """Create a progress event."""
        return cls(
            type=EventType.INDEX_PROGRESS.value,
            documentId=document_id,
            jobId=job_id,
            userId=user_id,
            stage=stage,
            progress=progress,
            message=message
        )
    
    @classmethod
    def complete(
        cls,
        document_id: str,
        job_id: str,
        user_id: str
    ) -> 'IndexProgressEvent':
        """Create a completion event."""
        return cls(
            type=EventType.INDEX_COMPLETE.value,
            documentId=document_id,
            jobId=job_id,
            userId=user_id,
            stage=ProgressStage.COMPLETE.value,
            progress=100,
            message="Indexing complete"
        )
    
    @classmethod
    def failed(
        cls,
        document_id: str,
        job_id: str,
        user_id: str,
        error_message: str
    ) -> 'IndexProgressEvent':
        """Create a failure event."""
        return cls(
            type=EventType.INDEX_FAILED.value,
            documentId=document_id,
            jobId=job_id,
            userId=user_id,
            stage=ProgressStage.FAILED.value,
            progress=0,
            message=error_message
        )


# Redis pub/sub channel name pattern
# Events are published to: index_progress:{user_id}
REDIS_CHANNEL_PREFIX = "index_progress"


def get_redis_channel(user_id: str) -> str:
    """Get the Redis pub/sub channel name for a user."""
    return f"{REDIS_CHANNEL_PREFIX}:{user_id}"
