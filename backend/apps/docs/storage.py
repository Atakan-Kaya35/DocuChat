"""
File storage service for document uploads.

Handles saving and retrieving files from the local filesystem.
MVP approach: files are stored in a mounted volume at /data/uploads.
"""
import os
import logging
from pathlib import Path
from typing import BinaryIO, Optional
from django.conf import settings

logger = logging.getLogger(__name__)


class StorageError(Exception):
    """Base exception for storage operations."""
    pass


class FileStorage:
    """
    Simple file storage for uploaded documents.
    
    Files are stored at: {UPLOAD_ROOT}/{document_id}.{extension}
    """
    
    def __init__(self, root: Optional[Path] = None):
        self.root = root or settings.UPLOAD_ROOT
        self._ensure_root_exists()
    
    def _ensure_root_exists(self) -> None:
        """Create the upload root directory if it doesn't exist."""
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            logger.info(f"Upload root ensured at: {self.root}")
        except OSError as e:
            logger.error(f"Failed to create upload root {self.root}: {e}")
            raise StorageError(f"Cannot create upload directory: {e}")
    
    def save(self, document_id: str, extension: str, file: BinaryIO) -> str:
        """
        Save a file to storage.
        
        Args:
            document_id: UUID of the document
            extension: File extension (e.g., '.pdf')
            file: File-like object with read() method
            
        Returns:
            Relative storage path (e.g., 'abc123.pdf')
            
        Raises:
            StorageError: If file cannot be saved
        """
        # Clean extension
        if not extension.startswith('.'):
            extension = f'.{extension}'
        
        filename = f"{document_id}{extension}"
        filepath = self.root / filename
        
        try:
            with open(filepath, 'wb') as dest:
                # Read and write in chunks to handle large files
                chunk_size = 8192
                while True:
                    chunk = file.read(chunk_size)
                    if not chunk:
                        break
                    dest.write(chunk)
            
            logger.info(f"Saved file: {filename} ({filepath.stat().st_size} bytes)")
            return filename
            
        except OSError as e:
            logger.error(f"Failed to save file {filename}: {e}")
            raise StorageError(f"Failed to save file: {e}")
    
    def get_path(self, storage_path: str) -> Path:
        """
        Get the full filesystem path for a stored file.
        
        Args:
            storage_path: Relative path returned by save()
            
        Returns:
            Full Path object
        """
        return self.root / storage_path
    
    def exists(self, storage_path: str) -> bool:
        """Check if a file exists in storage."""
        return (self.root / storage_path).exists()
    
    def delete(self, storage_path: str) -> bool:
        """
        Delete a file from storage.
        
        Args:
            storage_path: Relative path to delete
            
        Returns:
            True if deleted, False if file didn't exist
        """
        filepath = self.root / storage_path
        try:
            if filepath.exists():
                filepath.unlink()
                logger.info(f"Deleted file: {storage_path}")
                return True
            return False
        except OSError as e:
            logger.error(f"Failed to delete file {storage_path}: {e}")
            raise StorageError(f"Failed to delete file: {e}")
    
    def get_size(self, storage_path: str) -> int:
        """Get file size in bytes."""
        filepath = self.root / storage_path
        if not filepath.exists():
            raise StorageError(f"File not found: {storage_path}")
        return filepath.stat().st_size


# Singleton instance
_storage: Optional[FileStorage] = None


def get_storage() -> FileStorage:
    """Get the file storage instance (lazy initialization)."""
    global _storage
    if _storage is None:
        _storage = FileStorage()
    return _storage
