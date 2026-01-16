"""
Indexing worker - processes documents through the RAG pipeline.

This worker:
1. Claims queued jobs atomically (SELECT FOR UPDATE SKIP LOCKED)
2. Extracts text from documents
3. Chunks text into overlapping segments
4. Generates embeddings via Ollama
5. Stores chunks with vectors in pgvector

Run as: python manage.py run_worker
"""
import os
import sys
import time
import signal
import logging
from pathlib import Path
from typing import Optional
from datetime import datetime

import django
from django.db import transaction, connection
from django.conf import settings
from django.utils import timezone

# Ensure Django is set up
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.docs.models import Document, IndexJob, DocumentStatus, IndexJobStatus, IndexJobStage
from apps.indexing.models import DocumentChunk
from apps.indexing.extractor import extract_text, save_extracted_text, ExtractionError
from apps.indexing.chunker import chunk_text, TextChunk
from apps.indexing.embedder import generate_embedding, EmbeddingError, test_ollama_connection
from apps.indexing.publisher import publish_progress, publish_complete, publish_failed
from apps.indexing.retry import (
    retry_with_backoff,
    EMBEDDING_RETRY_CONFIG,
    RetryExhausted
)
from apps.authn.audit import audit_indexing_started, audit_indexing_completed, audit_indexing_failed

logger = logging.getLogger(__name__)

# Configuration
POLL_INTERVAL = 2  # seconds between job checks
MAX_CONSECUTIVE_ERRORS = 5  # Stop if too many errors in a row
HEARTBEAT_FILE = '/tmp/worker_heartbeat'


def touch_heartbeat():
    """Touch heartbeat file for health checks."""
    try:
        Path(HEARTBEAT_FILE).touch()
    except Exception as e:
        logger.warning(f"Failed to update heartbeat: {e}")


class IndexingWorker:
    """
    Worker that processes document indexing jobs.
    
    Uses SELECT FOR UPDATE SKIP LOCKED for safe concurrent job claiming.
    """
    
    def __init__(self):
        self.running = False
        self.consecutive_errors = 0
        self.upload_root = Path(settings.UPLOAD_ROOT)
        self.extracted_root = Path(getattr(settings, 'EXTRACTED_ROOT', '/data/extracted'))
        
        # Ensure directories exist
        self.extracted_root.mkdir(parents=True, exist_ok=True)
    
    def claim_job(self) -> Optional[IndexJob]:
        """
        Atomically claim the next queued job.
        
        Uses SELECT FOR UPDATE SKIP LOCKED to handle concurrent workers.
        
        Returns:
            The claimed IndexJob, or None if no jobs available
        """
        with transaction.atomic():
            # Use raw SQL for SKIP LOCKED which Django ORM doesn't support directly
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT id FROM index_jobs
                    WHERE status = %s
                    ORDER BY created_at ASC
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                """, [IndexJobStatus.QUEUED])
                
                row = cursor.fetchone()
                if not row:
                    return None
                
                job_id = row[0]
            
            # Now update the job to claim it
            job = IndexJob.objects.select_for_update().get(id=job_id)
            job.status = IndexJobStatus.RUNNING
            job.stage = IndexJobStage.RECEIVED
            job.save(update_fields=['status', 'stage', 'updated_at'])
            
            # Also update document status
            job.document.status = DocumentStatus.INDEXING
            job.document.save(update_fields=['status', 'updated_at'])
            
            logger.info(f"Claimed job {job.id} for document {job.document.filename}")
            return job
    
    def update_job_progress(
        self,
        job: IndexJob,
        stage: str,
        progress: int,
        status: Optional[str] = None,
        message: Optional[str] = None
    ):
        """Update job progress and emit WebSocket event."""
        job.stage = stage
        job.progress = progress
        if status:
            job.status = status
        job.save(update_fields=['stage', 'progress', 'status', 'updated_at'])
        
        # Emit progress event via WebSocket
        publish_progress(
            document_id=str(job.document.id),
            job_id=str(job.id),
            user_id=job.document.owner_user_id,
            stage=stage,
            progress=progress,
            message=message
        )
    
    def fail_job(self, job: IndexJob, error_message: str):
        """Mark a job as failed and emit failure event."""
        logger.error(f"Job {job.id} failed: {error_message}")
        
        job.status = IndexJobStatus.FAILED
        job.error_message = error_message
        job.save(update_fields=['status', 'error_message', 'updated_at'])
        
        job.document.status = DocumentStatus.FAILED
        job.document.save(update_fields=['status', 'updated_at'])
        
        # Emit failure event via WebSocket
        publish_failed(
            document_id=str(job.document.id),
            job_id=str(job.id),
            user_id=job.document.owner_user_id,
            error_message=error_message
        )
    
    def complete_job(self, job: IndexJob):
        """Mark a job as complete and emit completion event."""
        logger.info(f"Job {job.id} completed successfully")
        
        job.status = IndexJobStatus.COMPLETE
        job.stage = IndexJobStage.STORE  # Final stage
        job.progress = 100
        job.save(update_fields=['status', 'stage', 'progress', 'updated_at'])
        
        job.document.status = DocumentStatus.INDEXED
        job.document.save(update_fields=['status', 'updated_at'])
        
        # Emit completion event via WebSocket
        publish_complete(
            document_id=str(job.document.id),
            job_id=str(job.id),
            user_id=job.document.owner_user_id
        )
    
    def process_job(self, job: IndexJob):
        """
        Process a single indexing job through all stages.
        
        Stages:
        1. EXTRACT: Extract text from document
        2. CHUNK: Split text into overlapping chunks
        3. EMBED: Generate embeddings for each chunk
        4. STORE: Store chunks with vectors (happens during EMBED)
        """
        document = job.document
        doc_id = str(document.id)
        
        try:
            # Stage 1: EXTRACT
            self.update_job_progress(job, IndexJobStage.EXTRACT, 10)
            
            file_path = self.upload_root / document.storage_path
            if not file_path.exists():
                raise ExtractionError(f"File not found: {file_path}")
            
            text = extract_text(file_path, document.content_type)
            
            if not text.strip():
                raise ExtractionError("No text extracted from document")
            
            # Save extracted text as sidecar file
            save_extracted_text(doc_id, text, self.extracted_root)
            
            logger.info(f"Extracted {len(text)} characters from {document.filename}")
            
            # Stage 2: CHUNK
            self.update_job_progress(job, IndexJobStage.CHUNK, 30)
            
            chunks = chunk_text(text)
            
            if not chunks:
                raise ExtractionError("No chunks generated from text")
            
            logger.info(f"Created {len(chunks)} chunks from {document.filename}")
            
            # Log some chunk previews
            for chunk in chunks[:3]:
                preview = chunk.text[:100].replace('\n', ' ')
                logger.debug(f"  Chunk {chunk.index}: {preview}...")
            
            # Stage 3 & 4: EMBED and STORE
            self.update_job_progress(job, IndexJobStage.EMBED, 40)
            
            total_chunks = len(chunks)
            
            for i, chunk in enumerate(chunks):
                # Generate embedding with retry
                try:
                    embedding = retry_with_backoff(
                        func=lambda c=chunk: generate_embedding(c.text),
                        config=EMBEDDING_RETRY_CONFIG,
                        exceptions=(EmbeddingError,),
                        on_retry=lambda attempt, err, backoff: logger.warning(
                            f"Embedding retry {attempt + 1} for chunk {i}: {err}"
                        )
                    )
                except RetryExhausted as e:
                    raise EmbeddingError(
                        f"Failed to embed chunk {i} after {e.attempts} attempts: {e.last_exception}"
                    )
                except EmbeddingError as e:
                    # Non-retriable error
                    raise EmbeddingError(f"Failed to embed chunk {i}: {e}")
                
                # Store chunk with embedding (upsert logic via unique constraint)
                # Using get_or_create + update pattern for idempotency
                chunk_obj, created = DocumentChunk.objects.update_or_create(
                    document=document,
                    chunk_index=chunk.index,
                    defaults={
                        'text': chunk.text,
                        'embedding': embedding
                    }
                )
                
                if created:
                    logger.debug(f"Created chunk {chunk.index} for {doc_id}")
                else:
                    logger.debug(f"Updated chunk {chunk.index} for {doc_id}")
                
                # Update progress (40% to 95%)
                progress = 40 + int((i + 1) / total_chunks * 55)
                self.update_job_progress(job, IndexJobStage.EMBED, progress)
            
            # Stage 4: STORE (final)
            self.update_job_progress(job, IndexJobStage.STORE, 98)
            
            # Verify chunks were stored
            stored_count = DocumentChunk.objects.filter(document=document).count()
            logger.info(f"Stored {stored_count} chunks for {document.filename}")
            
            # Complete
            self.complete_job(job)
            
        except ExtractionError as e:
            self.fail_job(job, f"Extraction error: {e}")
        except EmbeddingError as e:
            self.fail_job(job, f"Embedding error: {e}")
        except Exception as e:
            logger.exception(f"Unexpected error processing job {job.id}")
            self.fail_job(job, f"Unexpected error: {e}")
    
    def run_once(self) -> bool:
        """
        Try to claim and process one job.
        
        Returns:
            True if a job was processed, False if no jobs available
        """
        job = self.claim_job()
        
        if not job:
            return False
        
        self.process_job(job)
        self.consecutive_errors = 0
        return True
    
    def run(self):
        """
        Main worker loop.
        
        Continuously polls for jobs and processes them.
        """
        logger.info("Starting indexing worker...")
        
        # Test Ollama connection
        if not test_ollama_connection():
            logger.error("Cannot connect to Ollama. Worker will retry on each job.")
        
        self.running = True
        
        # Set up signal handlers for graceful shutdown
        def handle_signal(signum, frame):
            logger.info(f"Received signal {signum}, shutting down...")
            self.running = False
        
        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)
        
        while self.running:
            try:
                if self.run_once():
                    # Job was processed, immediately check for more
                    continue
                else:
                    # No jobs, wait before polling again
                    time.sleep(POLL_INTERVAL)
                    
            except Exception as e:
                logger.exception(f"Error in worker loop: {e}")
                self.consecutive_errors += 1
                
                if self.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    logger.error("Too many consecutive errors, stopping worker")
                    break
                
                # Back off on errors
                time.sleep(POLL_INTERVAL * 2)
        
        logger.info("Worker stopped")


def main():
    """Entry point for the worker."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(levelname)s %(asctime)s %(name)s: %(message)s'
    )
    
    worker = IndexingWorker()
    worker.run()


if __name__ == '__main__':
    main()
