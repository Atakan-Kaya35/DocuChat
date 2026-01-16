"""
Text extraction from various document formats.

Supports:
- .txt: UTF-8 text (with fallback for encoding errors)
- .md: UTF-8 markdown
- .pdf: Best-effort text extraction using PyMuPDF
"""
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class ExtractionError(Exception):
    """Raised when text extraction fails."""
    pass


def extract_text_from_txt(file_path: Path) -> str:
    """
    Extract text from a plain text file.
    
    Args:
        file_path: Path to the .txt file
        
    Returns:
        The text content
        
    Raises:
        ExtractionError: If file cannot be read
    """
    try:
        # Try UTF-8 first, then fallback with error handling
        try:
            return file_path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            logger.warning(f"UTF-8 decode failed for {file_path}, using errors='ignore'")
            return file_path.read_text(encoding='utf-8', errors='ignore')
    except Exception as e:
        raise ExtractionError(f"Failed to read text file: {e}")


def extract_text_from_md(file_path: Path) -> str:
    """
    Extract text from a markdown file.
    
    For MVP, we keep the markdown as-is (no conversion to plain text).
    The chunker and embedder can handle markdown syntax.
    
    Args:
        file_path: Path to the .md file
        
    Returns:
        The markdown content
        
    Raises:
        ExtractionError: If file cannot be read
    """
    try:
        try:
            return file_path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            logger.warning(f"UTF-8 decode failed for {file_path}, using errors='ignore'")
            return file_path.read_text(encoding='utf-8', errors='ignore')
    except Exception as e:
        raise ExtractionError(f"Failed to read markdown file: {e}")


def extract_text_from_pdf(file_path: Path) -> str:
    """
    Extract text from a PDF file using PyMuPDF.
    
    This is a best-effort extraction - some PDFs (scanned, image-based)
    may not yield text. We don't do OCR in MVP.
    
    Args:
        file_path: Path to the .pdf file
        
    Returns:
        Extracted text from all pages
        
    Raises:
        ExtractionError: If extraction fails
    """
    try:
        import fitz  # PyMuPDF
        
        text_parts = []
        
        with fitz.open(file_path) as doc:
            for page_num, page in enumerate(doc):
                page_text = page.get_text()
                if page_text.strip():
                    text_parts.append(page_text)
                    
        if not text_parts:
            logger.warning(f"No text extracted from PDF {file_path} (may be image-based)")
            return ""
            
        return "\n\n".join(text_parts)
        
    except ImportError:
        raise ExtractionError("PyMuPDF (fitz) not installed")
    except Exception as e:
        raise ExtractionError(f"Failed to extract text from PDF: {e}")


def extract_text(file_path: Path, content_type: Optional[str] = None) -> str:
    """
    Extract text from a document file.
    
    Determines the extraction method based on file extension or content type.
    
    Args:
        file_path: Path to the document file
        content_type: Optional MIME type hint
        
    Returns:
        Extracted text content
        
    Raises:
        ExtractionError: If extraction fails or format not supported
    """
    suffix = file_path.suffix.lower()
    
    logger.info(f"Extracting text from {file_path} (suffix={suffix}, content_type={content_type})")
    
    if suffix == '.txt' or content_type == 'text/plain':
        return extract_text_from_txt(file_path)
    
    elif suffix in ('.md', '.markdown') or content_type in ('text/markdown', 'text/x-markdown'):
        return extract_text_from_md(file_path)
    
    elif suffix == '.pdf' or content_type == 'application/pdf':
        return extract_text_from_pdf(file_path)
    
    else:
        raise ExtractionError(f"Unsupported file format: {suffix}")


def save_extracted_text(document_id: str, text: str, output_dir: Path) -> Path:
    """
    Save extracted text as a sidecar file.
    
    Args:
        document_id: UUID of the document
        text: Extracted text content
        output_dir: Directory to save the file (e.g., /data/extracted)
        
    Returns:
        Path to the saved file
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{document_id}.txt"
    output_path.write_text(text, encoding='utf-8')
    logger.info(f"Saved extracted text to {output_path} ({len(text)} chars)")
    return output_path


def load_extracted_text(document_id: str, output_dir: Path) -> Optional[str]:
    """
    Load previously extracted text from sidecar file.
    
    Args:
        document_id: UUID of the document
        output_dir: Directory where extracted files are stored
        
    Returns:
        The extracted text, or None if not found
    """
    file_path = output_dir / f"{document_id}.txt"
    if file_path.exists():
        return file_path.read_text(encoding='utf-8')
    return None
