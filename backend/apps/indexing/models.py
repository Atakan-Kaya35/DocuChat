"""
Document chunk model for storing text chunks with embeddings.
"""
import uuid
from django.db import models
from pgvector.django import VectorField

from apps.docs.models import Document


class DocumentChunk(models.Model):
    """
    A text chunk from a document with its embedding vector.
    
    Chunks are created during the indexing pipeline and stored
    with their vector embeddings for similarity search.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Link to parent document
    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name='chunks',
        help_text="The source document"
    )
    
    # Chunk ordering (0-indexed)
    chunk_index = models.PositiveIntegerField(
        help_text="Index of this chunk within the document (0-based)"
    )
    
    # Chunk text content
    text = models.TextField(
        help_text="The text content of this chunk"
    )
    
    # Vector embedding (dimension depends on model, nomic-embed-text uses 768)
    embedding = VectorField(
        dimensions=768,
        null=True,
        blank=True,
        help_text="Vector embedding from Ollama nomic-embed-text"
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'doc_chunks'
        ordering = ['document', 'chunk_index']
        # Unique constraint prevents duplicate chunks
        constraints = [
            models.UniqueConstraint(
                fields=['document', 'chunk_index'],
                name='unique_document_chunk'
            )
        ]
        indexes = [
            models.Index(fields=['document', 'chunk_index']),
        ]

    def __str__(self):
        preview = self.text[:50] + '...' if len(self.text) > 50 else self.text
        return f"Chunk {self.chunk_index} of {self.document.filename}: {preview}"
