"""
Document upload and management views.

Provides endpoints for:
- POST /api/docs/upload - Upload a new document
- GET /api/docs - List user's documents
- GET /api/docs/<id> - Get document details
"""
import os
import logging
from pathlib import Path
from django.conf import settings
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt

from apps.authn.middleware import auth_required
from .models import Document, IndexJob, DocumentStatus, IndexJobStatus, IndexJobStage
from .storage import get_storage, StorageError

logger = logging.getLogger(__name__)


def get_extension(filename: str) -> str:
    """Extract file extension from filename."""
    return Path(filename).suffix.lower()


def validate_content_type(content_type: str) -> bool:
    """Check if content type is allowed."""
    return content_type in settings.ALLOWED_CONTENT_TYPES


def validate_extension(filename: str) -> bool:
    """Check if file extension is allowed."""
    ext = get_extension(filename)
    return ext in settings.ALLOWED_EXTENSIONS


def normalize_content_type(content_type: str, filename: str) -> str:
    """
    Normalize content type, using file extension as fallback.
    
    Some browsers/clients send incorrect MIME types, so we also check extension.
    """
    ext = get_extension(filename)
    
    # Map extensions to MIME types
    ext_to_mime = {
        '.pdf': 'application/pdf',
        '.txt': 'text/plain',
        '.md': 'text/markdown',
        '.markdown': 'text/markdown',
    }
    
    # If content type is generic, use extension-based type
    if content_type in ('application/octet-stream', 'binary/octet-stream', ''):
        return ext_to_mime.get(ext, content_type)
    
    return content_type


@csrf_exempt
@require_http_methods(["POST"])
@auth_required
def upload_document(request):
    """
    Upload a new document.
    
    POST /api/docs/upload
    
    Accepts multipart/form-data with a 'file' field.
    
    Allowed file types: PDF, TXT, MD
    Max size: 50MB (configurable)
    
    Returns:
        {
            "documentId": "uuid",
            "jobId": "uuid", 
            "status": "QUEUED",
            "filename": "original.pdf"
        }
    """
    user_id = request.user_claims.sub
    
    if 'file' not in request.FILES:
        return JsonResponse(
            {'error': 'No file provided', 'code': 'MISSING_FILE'},
            status=400
        )
    
    uploaded_file = request.FILES['file']
    filename = uploaded_file.name
    content_type = uploaded_file.content_type
    size_bytes = uploaded_file.size
    
    logger.info(f"Upload request: {filename}, {content_type}, {size_bytes} bytes from user {user_id}")
    
    # Validate file size
    if size_bytes > settings.MAX_UPLOAD_SIZE:
        max_mb = settings.MAX_UPLOAD_SIZE // (1024 * 1024)
        return JsonResponse(
            {
                'error': f'File too large. Maximum size is {max_mb}MB',
                'code': 'FILE_TOO_LARGE',
                'maxSize': settings.MAX_UPLOAD_SIZE
            },
            status=400
        )
    
    # Validate extension first
    if not validate_extension(filename):
        return JsonResponse(
            {
                'error': 'Invalid file type. Allowed: PDF, TXT, MD',
                'code': 'INVALID_FILE_TYPE',
                'allowedExtensions': settings.ALLOWED_EXTENSIONS
            },
            status=400
        )
    
    # Normalize and validate content type
    content_type = normalize_content_type(content_type, filename)
    if not validate_content_type(content_type):
        return JsonResponse(
            {
                'error': 'Invalid content type. Allowed: PDF, TXT, MD',
                'code': 'INVALID_CONTENT_TYPE',
                'allowedTypes': settings.ALLOWED_CONTENT_TYPES
            },
            status=400
        )
    
    try:
        with transaction.atomic():
            # Create document record first to get the ID
            document = Document.objects.create(
                owner_user_id=user_id,
                filename=filename,
                content_type=content_type,
                size_bytes=size_bytes,
                storage_path='',  # Will update after saving file
                status=DocumentStatus.UPLOADED
            )
            
            # Save file to storage
            extension = get_extension(filename)
            storage = get_storage()
            storage_path = storage.save(str(document.id), extension, uploaded_file)
            
            # Update document with storage path
            document.storage_path = storage_path
            document.status = DocumentStatus.QUEUED
            document.save(update_fields=['storage_path', 'status', 'updated_at'])
            
            # Create index job
            job = IndexJob.objects.create(
                document=document,
                status=IndexJobStatus.QUEUED,
                stage=IndexJobStage.RECEIVED,
                progress=0
            )
            
            logger.info(f"Document created: {document.id}, job: {job.id}")
            
            return JsonResponse({
                'documentId': str(document.id),
                'jobId': str(job.id),
                'status': document.status,
                'filename': document.filename
            }, status=201)
            
    except StorageError as e:
        logger.error(f"Storage error during upload: {e}")
        return JsonResponse(
            {'error': 'Failed to store file', 'code': 'STORAGE_ERROR'},
            status=500
        )
    except Exception as e:
        logger.exception(f"Unexpected error during upload: {e}")
        return JsonResponse(
            {'error': 'Internal server error', 'code': 'INTERNAL_ERROR'},
            status=500
        )


@csrf_exempt
@require_http_methods(["GET"])
@auth_required
def list_documents(request):
    """
    List all documents for the authenticated user.
    
    GET /api/docs
    
    Returns:
        {
            "documents": [
                {
                    "id": "uuid",
                    "filename": "document.pdf",
                    "contentType": "application/pdf",
                    "sizeBytes": 12345,
                    "status": "QUEUED",
                    "createdAt": "2024-01-01T00:00:00Z",
                    "latestJob": {
                        "id": "uuid",
                        "status": "QUEUED",
                        "stage": "RECEIVED",
                        "progress": 0
                    }
                }
            ]
        }
    """
    user_id = request.user_claims.sub
    
    documents = Document.objects.filter(
        owner_user_id=user_id
    ).prefetch_related('index_jobs').order_by('-created_at')
    
    docs_list = []
    for doc in documents:
        # Get latest job
        latest_job = doc.index_jobs.order_by('-created_at').first()
        
        doc_data = {
            'id': str(doc.id),
            'filename': doc.filename,
            'contentType': doc.content_type,
            'sizeBytes': doc.size_bytes,
            'status': doc.status,
            'createdAt': doc.created_at.isoformat(),
            'updatedAt': doc.updated_at.isoformat(),
        }
        
        if latest_job:
            doc_data['latestJob'] = {
                'id': str(latest_job.id),
                'status': latest_job.status,
                'stage': latest_job.stage,
                'progress': latest_job.progress,
                'errorMessage': latest_job.error_message,
            }
        
        docs_list.append(doc_data)
    
    return JsonResponse({'documents': docs_list})


@csrf_exempt
@require_http_methods(["GET"])
@auth_required
def get_document(request, document_id):
    """
    Get details for a specific document.
    
    GET /api/docs/<document_id>
    
    Returns:
        {
            "id": "uuid",
            "filename": "document.pdf",
            "contentType": "application/pdf",
            "sizeBytes": 12345,
            "status": "QUEUED",
            "createdAt": "2024-01-01T00:00:00Z",
            "jobs": [...]
        }
    """
    user_id = request.user_claims.sub
    
    try:
        document = Document.objects.prefetch_related('index_jobs').get(id=document_id)
    except Document.DoesNotExist:
        return JsonResponse(
            {'error': 'Document not found', 'code': 'NOT_FOUND'},
            status=404
        )
    
    # Check ownership
    if document.owner_user_id != user_id:
        return JsonResponse(
            {'error': 'Document not found', 'code': 'NOT_FOUND'},
            status=404
        )
    
    # Get all jobs for this document
    jobs = []
    for job in document.index_jobs.order_by('-created_at'):
        jobs.append({
            'id': str(job.id),
            'status': job.status,
            'stage': job.stage,
            'progress': job.progress,
            'errorMessage': job.error_message,
            'createdAt': job.created_at.isoformat(),
            'updatedAt': job.updated_at.isoformat(),
        })
    
    return JsonResponse({
        'id': str(document.id),
        'filename': document.filename,
        'contentType': document.content_type,
        'sizeBytes': document.size_bytes,
        'storagePath': document.storage_path,
        'status': document.status,
        'createdAt': document.created_at.isoformat(),
        'updatedAt': document.updated_at.isoformat(),
        'jobs': jobs
    })
