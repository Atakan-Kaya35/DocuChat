"""
Deterministic text chunking for document indexing.

Chunking is designed to be:
- Deterministic: Same input always produces same chunks
- Idempotent: Re-running produces identical chunk IDs
- Overlap-aware: Chunks have configurable overlap for context continuity
"""
import re
import logging
from dataclasses import dataclass
from typing import List

logger = logging.getLogger(__name__)

# Default chunking parameters
DEFAULT_CHUNK_SIZE = 1000  # characters (approximately 250 tokens)
DEFAULT_CHUNK_OVERLAP = 150  # characters of overlap between chunks
MIN_CHUNK_SIZE = 100  # Minimum size for final chunk


@dataclass
class TextChunk:
    """A chunk of text with its index."""
    index: int
    text: str
    start_char: int
    end_char: int
    
    @property
    def char_count(self) -> int:
        return len(self.text)


def normalize_whitespace(text: str) -> str:
    """
    Normalize whitespace in text for consistent chunking.
    
    - Converts all whitespace sequences to single spaces
    - Preserves paragraph breaks (double newlines)
    - Strips leading/trailing whitespace
    
    Args:
        text: Raw text input
        
    Returns:
        Normalized text
    """
    # First, normalize line endings
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    
    # Preserve paragraph breaks by replacing with placeholder
    text = re.sub(r'\n\s*\n', '\n\n', text)
    
    # Replace multiple spaces/tabs with single space
    text = re.sub(r'[^\S\n]+', ' ', text)
    
    # Clean up lines
    lines = text.split('\n')
    lines = [line.strip() for line in lines]
    text = '\n'.join(lines)
    
    # Remove excessive newlines (more than 2 in a row)
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    return text.strip()


def find_break_point(text: str, target_pos: int, window: int = 100) -> int:
    """
    Find a good break point near the target position.
    
    Tries to break at:
    1. Paragraph boundary (double newline)
    2. Sentence boundary (. ! ? followed by space/newline)
    3. Clause boundary (comma, semicolon)
    4. Word boundary (space)
    5. Falls back to exact position if nothing found
    
    Args:
        text: The text to search
        target_pos: Ideal position to break at
        window: How far to search for a good break point
        
    Returns:
        Best break position found
    """
    if target_pos >= len(text):
        return len(text)
    
    # Define search window
    start = max(0, target_pos - window // 2)
    end = min(len(text), target_pos + window // 2)
    
    # Search for break points in order of preference
    search_text = text[start:end]
    
    # 1. Paragraph boundary
    para_match = re.search(r'\n\n', search_text)
    if para_match:
        return start + para_match.end()
    
    # 2. Sentence boundary (prioritize those closest to target)
    sentence_matches = list(re.finditer(r'[.!?]\s', search_text))
    if sentence_matches:
        # Find the one closest to the relative target position
        rel_target = target_pos - start
        best = min(sentence_matches, key=lambda m: abs(m.end() - rel_target))
        return start + best.end()
    
    # 3. Clause boundary
    clause_matches = list(re.finditer(r'[,;:]\s', search_text))
    if clause_matches:
        rel_target = target_pos - start
        best = min(clause_matches, key=lambda m: abs(m.end() - rel_target))
        return start + best.end()
    
    # 4. Word boundary
    space_matches = list(re.finditer(r'\s', search_text))
    if space_matches:
        rel_target = target_pos - start
        best = min(space_matches, key=lambda m: abs(m.end() - rel_target))
        return start + best.end()
    
    # 5. Fallback to exact position
    return target_pos


def chunk_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    normalize: bool = True
) -> List[TextChunk]:
    """
    Split text into overlapping chunks.
    
    This is a deterministic chunking algorithm that:
    - Normalizes whitespace for consistency
    - Creates fixed-size chunks with overlap
    - Tries to break at sentence/paragraph boundaries
    - Produces stable chunk indices
    
    Args:
        text: The text to chunk
        chunk_size: Target size for each chunk in characters
        chunk_overlap: Number of characters to overlap between chunks
        normalize: Whether to normalize whitespace first
        
    Returns:
        List of TextChunk objects
    """
    if normalize:
        text = normalize_whitespace(text)
    
    if not text:
        logger.warning("Empty text provided for chunking")
        return []
    
    # If text is smaller than chunk size, return as single chunk
    if len(text) <= chunk_size:
        return [TextChunk(index=0, text=text, start_char=0, end_char=len(text))]
    
    chunks = []
    current_pos = 0
    chunk_index = 0
    
    while current_pos < len(text):
        # Calculate end position for this chunk
        end_pos = current_pos + chunk_size
        
        if end_pos >= len(text):
            # This is the last chunk
            chunk_text_content = text[current_pos:].strip()
            if len(chunk_text_content) >= MIN_CHUNK_SIZE or not chunks:
                # Only add if substantial or if it's the only chunk
                chunks.append(TextChunk(
                    index=chunk_index,
                    text=chunk_text_content,
                    start_char=current_pos,
                    end_char=len(text)
                ))
            elif chunks and chunk_text_content:
                # Append small final chunk to the previous one
                prev = chunks[-1]
                merged_text = prev.text + " " + chunk_text_content
                chunks[-1] = TextChunk(
                    index=prev.index,
                    text=merged_text,
                    start_char=prev.start_char,
                    end_char=len(text)
                )
            break
        
        # Find a good break point
        break_pos = find_break_point(text, end_pos)
        
        # Extract chunk
        chunk_text_content = text[current_pos:break_pos].strip()
        
        if chunk_text_content:
            chunks.append(TextChunk(
                index=chunk_index,
                text=chunk_text_content,
                start_char=current_pos,
                end_char=break_pos
            ))
            chunk_index += 1
        
        # Move position, accounting for overlap
        current_pos = break_pos - chunk_overlap
        
        # Ensure we make progress
        if current_pos <= chunks[-1].start_char if chunks else 0:
            current_pos = break_pos
    
    logger.info(f"Created {len(chunks)} chunks from {len(text)} characters")
    
    return chunks
