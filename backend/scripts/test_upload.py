"""
Test script for document upload functionality.
Run with: python manage.py shell < scripts/test_upload.py
"""
import os
import io
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.docs.models import Document, IndexJob, DocumentStatus, IndexJobStatus
from apps.docs.storage import get_storage

# Create a test file
test_content = b"This is a test document for DocuChat."
test_file = io.BytesIO(test_content)

# Test storage
storage = get_storage()
print(f"Storage root: {storage.root}")
print(f"Storage root exists: {storage.root.exists()}")

# Create a document manually (simulating what the view does)
doc = Document.objects.create(
    owner_user_id="test-user-id",
    filename="test.txt",
    content_type="text/plain",
    size_bytes=len(test_content),
    storage_path="",
    status=DocumentStatus.UPLOADED
)
print(f"Created document: {doc.id}")

# Save file
storage_path = storage.save(str(doc.id), ".txt", test_file)
print(f"Saved file to: {storage_path}")

# Update document
doc.storage_path = storage_path
doc.status = DocumentStatus.QUEUED
doc.save()

# Create index job
job = IndexJob.objects.create(
    document=doc,
    status=IndexJobStatus.QUEUED
)
print(f"Created index job: {job.id}")

# Verify
print(f"\nDocument: {doc.id}")
print(f"  Status: {doc.status}")
print(f"  Storage path: {doc.storage_path}")
print(f"  File exists: {storage.exists(storage_path)}")
print(f"\nJob: {job.id}")
print(f"  Status: {job.status}")
print(f"  Stage: {job.stage}")

# List all documents
print(f"\nAll documents: {Document.objects.count()}")
