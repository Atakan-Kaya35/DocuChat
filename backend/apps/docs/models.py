"""
Document and IndexJob models for DocuChat.

This module defines the core data models for document storage and indexing.
"""
import uuid
from django.db import models


class DocumentStatus(models.TextChoices):
    """Status of a document in the indexing pipeline."""
    UPLOADED = 'UPLOADED', 'Uploaded'
    QUEUED = 'QUEUED', 'Queued for indexing'
    INDEXING = 'INDEXING', 'Currently indexing'
    INDEXED = 'INDEXED', 'Successfully indexed'
    FAILED = 'FAILED', 'Indexing failed'


class IndexJobStatus(models.TextChoices):
    """Status of an indexing job."""
    QUEUED = 'QUEUED', 'Queued'
    RUNNING = 'RUNNING', 'Running'
    COMPLETE = 'COMPLETE', 'Complete'
    FAILED = 'FAILED', 'Failed'


class IndexJobStage(models.TextChoices):
    """Current stage of an indexing job."""
    RECEIVED = 'RECEIVED', 'Received'
    EXTRACT = 'EXTRACT', 'Extracting text'
    CHUNK = 'CHUNK', 'Chunking text'
    EMBED = 'EMBED', 'Generating embeddings'
    STORE = 'STORE', 'Storing in vector DB'


class Document(models.Model):
    """
    A document uploaded by a user for RAG indexing.
    
    Documents are stored on disk and their metadata is tracked here.
    The indexing pipeline processes documents through various stages
    tracked by IndexJob.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Owner is the Keycloak 'sub' claim (user ID)
    owner_user_id = models.CharField(
        max_length=255,
        db_index=True,
        help_text="Keycloak user ID (sub claim)"
    )
    
    # File metadata
    filename = models.CharField(
        max_length=255,
        help_text="Original filename"
    )
    content_type = models.CharField(
        max_length=100,
        help_text="MIME type of the file"
    )
    size_bytes = models.PositiveIntegerField(
        help_text="File size in bytes"
    )
    
    # Storage location
    storage_path = models.CharField(
        max_length=500,
        help_text="Path to file on disk (relative to upload root)"
    )
    
    # Processing status
    status = models.CharField(
        max_length=20,
        choices=DocumentStatus.choices,
        default=DocumentStatus.UPLOADED,
        db_index=True,
        help_text="Current status in the indexing pipeline"
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'documents'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['owner_user_id', 'created_at']),
        ]

    def __str__(self):
        return f"{self.filename} ({self.status})"


class IndexJob(models.Model):
    """
    An indexing job for a document.
    
    Tracks the progress of document processing through the RAG pipeline.
    Each document can have multiple jobs (e.g., for re-indexing).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Link to document
    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name='index_jobs',
        help_text="The document being indexed"
    )
    
    # Job status
    status = models.CharField(
        max_length=20,
        choices=IndexJobStatus.choices,
        default=IndexJobStatus.QUEUED,
        db_index=True,
        help_text="Current job status"
    )
    
    # Current processing stage
    stage = models.CharField(
        max_length=20,
        choices=IndexJobStage.choices,
        default=IndexJobStage.RECEIVED,
        help_text="Current processing stage"
    )
    
    # Progress tracking (0-100)
    progress = models.PositiveSmallIntegerField(
        default=0,
        help_text="Progress percentage (0-100)"
    )
    
    # Error tracking
    error_message = models.TextField(
        null=True,
        blank=True,
        help_text="Error message if job failed"
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'index_jobs'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['document', 'created_at']),
        ]

    def __str__(self):
        return f"Job {self.id} for {self.document.filename} ({self.status}/{self.stage})"
