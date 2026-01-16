"""
Redis-backed rate limiter.

Implements both fixed window and token bucket algorithms.
See OPERATIONS.md for policy details.
"""
import time
import logging
import functools
from dataclasses import dataclass
from typing import Optional, Callable
from urllib.parse import urlparse

import redis
from django.conf import settings
from django.http import JsonResponse

logger = logging.getLogger(__name__)


# Rate limit configurations
UPLOAD_RATE_LIMIT = {
    'algorithm': 'fixed_window',
    'window_seconds': 60,
    'max_requests': 10,
}

ASK_RATE_LIMIT = {
    'algorithm': 'token_bucket',
    'bucket_capacity': 5,
    'refill_rate': 0.2,  # tokens per second (12/minute)
}


@dataclass
class RateLimitResult:
    """Result of a rate limit check."""
    allowed: bool
    limit: int
    remaining: int
    reset_at: int  # Unix timestamp
    retry_after: Optional[int] = None  # seconds to wait if blocked


def get_redis_client() -> redis.Redis:
    """Get a Redis client from the configured URL."""
    redis_url = getattr(settings, 'REDIS_URL', 'redis://redis:6379/0')
    return redis.from_url(redis_url, decode_responses=True)


def is_rate_limiting_disabled() -> bool:
    """Check if rate limiting is disabled (dev mode only)."""
    import os
    return os.getenv('DISABLE_RATE_LIMITING', '').lower() in ('true', '1', 'yes')


# Lua script for atomic fixed window rate limiting
FIXED_WINDOW_SCRIPT = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local now = tonumber(ARGV[3])

-- Calculate window start
local window_start = math.floor(now / window) * window
local window_key = key .. ":" .. window_start

-- Get current count
local current = tonumber(redis.call('GET', window_key) or '0')

-- Check if over limit
if current >= limit then
    local reset_at = window_start + window
    local retry_after = reset_at - now
    return {0, limit, 0, reset_at, retry_after}
end

-- Increment and set expiry
redis.call('INCR', window_key)
redis.call('EXPIRE', window_key, window + 1)

local remaining = limit - current - 1
local reset_at = window_start + window
return {1, limit, remaining, reset_at, 0}
"""


# Lua script for atomic token bucket rate limiting
TOKEN_BUCKET_SCRIPT = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])

-- Get bucket state
local state = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens = tonumber(state[1]) or capacity
local last_refill = tonumber(state[2]) or now

-- Calculate tokens to add based on time elapsed
local elapsed = now - last_refill
local tokens_to_add = elapsed * refill_rate
tokens = math.min(capacity, tokens + tokens_to_add)

-- Check if we have a token
if tokens < 1 then
    local time_to_next = (1 - tokens) / refill_rate
    local retry_after = math.ceil(time_to_next)
    return {0, capacity, math.floor(tokens), 0, retry_after}
end

-- Consume a token
tokens = tokens - 1

-- Save state
redis.call('HMSET', key, 'tokens', tokens, 'last_refill', now)
redis.call('EXPIRE', key, 3600)  -- 1 hour TTL

return {1, capacity, math.floor(tokens), 0, 0}
"""


class RateLimiter:
    """Redis-backed rate limiter."""
    
    def __init__(self):
        self._redis: Optional[redis.Redis] = None
        self._fixed_window_script = None
        self._token_bucket_script = None
    
    @property
    def redis(self) -> redis.Redis:
        if self._redis is None:
            self._redis = get_redis_client()
        return self._redis
    
    def check_fixed_window(
        self,
        key: str,
        limit: int,
        window_seconds: int
    ) -> RateLimitResult:
        """
        Check rate limit using fixed window algorithm.
        
        Args:
            key: Rate limit key (e.g., "upload:user123")
            limit: Maximum requests per window
            window_seconds: Window size in seconds
            
        Returns:
            RateLimitResult with allow/deny and metadata
        """
        if self._fixed_window_script is None:
            self._fixed_window_script = self.redis.register_script(FIXED_WINDOW_SCRIPT)
        
        now = int(time.time())
        result = self._fixed_window_script(
            keys=[f"ratelimit:{key}"],
            args=[limit, window_seconds, now]
        )
        
        allowed, limit, remaining, reset_at, retry_after = result
        
        return RateLimitResult(
            allowed=bool(allowed),
            limit=limit,
            remaining=remaining,
            reset_at=reset_at,
            retry_after=retry_after if retry_after > 0 else None
        )
    
    def check_token_bucket(
        self,
        key: str,
        capacity: int,
        refill_rate: float
    ) -> RateLimitResult:
        """
        Check rate limit using token bucket algorithm.
        
        Args:
            key: Rate limit key (e.g., "ask:user123")
            capacity: Maximum tokens (burst capacity)
            refill_rate: Tokens added per second
            
        Returns:
            RateLimitResult with allow/deny and metadata
        """
        if self._token_bucket_script is None:
            self._token_bucket_script = self.redis.register_script(TOKEN_BUCKET_SCRIPT)
        
        now = time.time()
        result = self._token_bucket_script(
            keys=[f"ratelimit:{key}"],
            args=[capacity, refill_rate, now]
        )
        
        allowed, limit, remaining, _, retry_after = result
        
        return RateLimitResult(
            allowed=bool(allowed),
            limit=limit,
            remaining=remaining,
            reset_at=0,  # Token bucket doesn't have fixed reset
            retry_after=retry_after if retry_after > 0 else None
        )


# Singleton instance
_limiter: Optional[RateLimiter] = None


def get_limiter() -> RateLimiter:
    """Get the singleton rate limiter instance."""
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter


def check_upload_rate_limit(user_id: str) -> RateLimitResult:
    """Check rate limit for document upload."""
    if is_rate_limiting_disabled():
        return RateLimitResult(allowed=True, limit=999, remaining=999, reset_at=0)
    
    try:
        limiter = get_limiter()
        return limiter.check_fixed_window(
            key=f"upload:{user_id}",
            limit=UPLOAD_RATE_LIMIT['max_requests'],
            window_seconds=UPLOAD_RATE_LIMIT['window_seconds']
        )
    except redis.RedisError as e:
        logger.error(f"Redis error in rate limiting: {e}")
        # Fail open - allow request if Redis is down
        return RateLimitResult(allowed=True, limit=0, remaining=0, reset_at=0)


def check_ask_rate_limit(user_id: str) -> RateLimitResult:
    """Check rate limit for ask endpoint."""
    if is_rate_limiting_disabled():
        return RateLimitResult(allowed=True, limit=999, remaining=999, reset_at=0)
    
    try:
        limiter = get_limiter()
        return limiter.check_token_bucket(
            key=f"ask:{user_id}",
            capacity=ASK_RATE_LIMIT['bucket_capacity'],
            refill_rate=ASK_RATE_LIMIT['refill_rate']
        )
    except redis.RedisError as e:
        logger.error(f"Redis error in rate limiting: {e}")
        # Fail open - allow request if Redis is down
        return RateLimitResult(allowed=True, limit=0, remaining=0, reset_at=0)


def add_rate_limit_headers(response, result: RateLimitResult):
    """Add standard rate limit headers to a response."""
    response['X-RateLimit-Limit'] = str(result.limit)
    response['X-RateLimit-Remaining'] = str(result.remaining)
    if result.reset_at > 0:
        response['X-RateLimit-Reset'] = str(result.reset_at)
    return response


def rate_limit_response(result: RateLimitResult) -> JsonResponse:
    """Generate a 429 rate limit exceeded response."""
    response = JsonResponse(
        {
            'error': 'Rate limit exceeded',
            'code': 'RATE_LIMITED',
            'retryAfter': result.retry_after or 60
        },
        status=429
    )
    response['Retry-After'] = str(result.retry_after or 60)
    add_rate_limit_headers(response, result)
    return response


def rate_limited(check_func: Callable[[str], RateLimitResult]):
    """
    Decorator to apply rate limiting to a view.
    
    Usage:
        @rate_limited(check_upload_rate_limit)
        @auth_required
        def upload_document(request):
            ...
    
    Args:
        check_func: Function that takes user_id and returns RateLimitResult
    """
    def decorator(view_func):
        @functools.wraps(view_func)
        def wrapper(request, *args, **kwargs):
            # Get user ID from request (set by auth middleware)
            user_id = getattr(getattr(request, 'user_claims', None), 'sub', None)
            if not user_id:
                # No user ID - let auth middleware handle it
                return view_func(request, *args, **kwargs)
            
            # Check rate limit
            result = check_func(user_id)
            
            if not result.allowed:
                logger.warning(f"Rate limit exceeded for user {user_id}")
                return rate_limit_response(result)
            
            # Call view and add headers to response
            response = view_func(request, *args, **kwargs)
            add_rate_limit_headers(response, result)
            return response
        
        return wrapper
    return decorator
