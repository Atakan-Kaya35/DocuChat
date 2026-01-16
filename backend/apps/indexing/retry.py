"""
Retry utilities with exponential backoff.

Provides decorators and utilities for bounded retries with jitter.
See OPERATIONS.md for policy details.
"""
import time
import random
import logging
import functools
from typing import Callable, Type, Tuple, Optional

logger = logging.getLogger(__name__)


class RetryExhausted(Exception):
    """Raised when all retries have been exhausted."""
    
    def __init__(self, message: str, attempts: int, last_exception: Exception):
        super().__init__(message)
        self.attempts = attempts
        self.last_exception = last_exception


# Retry configuration for embedding generation (worker)
EMBEDDING_RETRY_CONFIG = {
    'max_retries': 3,        # Total 4 attempts (1 initial + 3 retries)
    'initial_backoff': 2.0,  # 2 seconds
    'backoff_multiplier': 2.0,
    'max_backoff': 30.0,
    'jitter_percent': 0.25,  # ±25%
}

# Retry configuration for LLM generation (ask endpoint)
GENERATION_RETRY_CONFIG = {
    'max_retries': 2,        # Total 3 attempts
    'initial_backoff': 1.0,  # 1 second
    'backoff_multiplier': 2.0,
    'max_backoff': 5.0,
    'jitter_percent': 0.10,  # ±10%
}


def calculate_backoff(
    attempt: int,
    initial_backoff: float,
    backoff_multiplier: float,
    max_backoff: float,
    jitter_percent: float
) -> float:
    """
    Calculate backoff time with exponential increase and jitter.
    
    Args:
        attempt: Current retry attempt (0-indexed)
        initial_backoff: Base backoff in seconds
        backoff_multiplier: Exponential multiplier
        max_backoff: Maximum backoff cap
        jitter_percent: Random jitter range (0.25 = ±25%)
    
    Returns:
        Backoff time in seconds
    """
    # Exponential backoff
    backoff = initial_backoff * (backoff_multiplier ** attempt)
    
    # Cap at max
    backoff = min(backoff, max_backoff)
    
    # Add jitter
    jitter_range = backoff * jitter_percent
    jitter = random.uniform(-jitter_range, jitter_range)
    backoff += jitter
    
    return max(0.0, backoff)


def is_retriable_error(exception: Exception) -> bool:
    """
    Determine if an exception is retriable.
    
    Returns True for:
    - Connection errors (network issues)
    - Timeout errors
    - 5xx status codes
    - Empty response errors
    
    Returns False for:
    - 4xx errors (client error, won't help to retry)
    - Validation errors
    - Configuration errors (e.g., model not found)
    """
    import requests
    from apps.indexing.embedder import EmbeddingError
    
    error_msg = str(exception).lower()
    
    # Connection and timeout are always retriable
    if isinstance(exception, (requests.exceptions.ConnectionError,
                               requests.exceptions.Timeout)):
        return True
    
    # Check error message patterns
    retriable_patterns = [
        'connection',
        'timeout',
        'timed out',
        'temporarily unavailable',
        '503',
        '502',
        '500',
        'overloaded',
        'busy',
        'no embedding in response',  # Empty response, might work on retry
    ]
    
    for pattern in retriable_patterns:
        if pattern in error_msg:
            return True
    
    # Non-retriable patterns
    non_retriable_patterns = [
        '404',
        '400',
        '401',
        '403',
        'model not found',
        'invalid',
        'not supported',
    ]
    
    for pattern in non_retriable_patterns:
        if pattern in error_msg:
            return False
    
    # Default: retriable for unknown errors (optimistic)
    return True


def retry_with_backoff(
    func: Callable,
    config: dict,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    on_retry: Optional[Callable[[int, Exception, float], None]] = None
):
    """
    Execute a function with retry and exponential backoff.
    
    Args:
        func: Callable to execute
        config: Retry configuration dict
        exceptions: Tuple of exception types to catch
        on_retry: Optional callback(attempt, exception, backoff) called before each retry
        
    Returns:
        Result of func() if successful
        
    Raises:
        RetryExhausted: If all retries fail
        Exception: If a non-retriable exception is raised
    """
    max_retries = config['max_retries']
    attempt = 0
    last_exception = None
    
    while attempt <= max_retries:
        try:
            return func()
        except exceptions as e:
            last_exception = e
            
            # Check if error is retriable
            if not is_retriable_error(e):
                logger.warning(f"Non-retriable error on attempt {attempt + 1}: {e}")
                raise
            
            # Check if we have retries left
            if attempt >= max_retries:
                break
            
            # Calculate backoff
            backoff = calculate_backoff(
                attempt,
                config['initial_backoff'],
                config['backoff_multiplier'],
                config['max_backoff'],
                config['jitter_percent']
            )
            
            logger.warning(
                f"Retriable error on attempt {attempt + 1}/{max_retries + 1}: {e}. "
                f"Retrying in {backoff:.2f}s"
            )
            
            if on_retry:
                on_retry(attempt, e, backoff)
            
            time.sleep(backoff)
            attempt += 1
    
    # All retries exhausted
    raise RetryExhausted(
        f"All {max_retries + 1} attempts failed. Last error: {last_exception}",
        attempts=attempt + 1,
        last_exception=last_exception
    )


def with_retry(
    config: dict,
    exceptions: Tuple[Type[Exception], ...] = (Exception,)
):
    """
    Decorator for adding retry logic to a function.
    
    Usage:
        @with_retry(EMBEDDING_RETRY_CONFIG, exceptions=(EmbeddingError,))
        def generate_embedding(text):
            ...
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return retry_with_backoff(
                lambda: func(*args, **kwargs),
                config=config,
                exceptions=exceptions
            )
        return wrapper
    return decorator
