"""
JWKS (JSON Web Key Set) fetching and caching for Keycloak JWT validation.
"""
import logging
import time
import threading
from typing import Optional, Dict, Any

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class JWKSCache:
    """
    Thread-safe JWKS cache with TTL and automatic refresh on key rotation.
    """
    
    def __init__(self, jwks_url: str, cache_ttl: int = 600):
        """
        Initialize JWKS cache.
        
        Args:
            jwks_url: URL to fetch JWKS from
            cache_ttl: Cache time-to-live in seconds (default 10 minutes)
        """
        self._jwks_url = jwks_url
        self._cache_ttl = cache_ttl
        self._keys: Dict[str, Dict[str, Any]] = {}
        self._last_fetch: float = 0
        self._lock = threading.RLock()
    
    def _fetch_jwks(self) -> Dict[str, Dict[str, Any]]:
        """
        Fetch JWKS from Keycloak and return as dict keyed by kid.
        """
        try:
            logger.debug(f"Fetching JWKS from {self._jwks_url}")
            response = requests.get(self._jwks_url, timeout=10)
            response.raise_for_status()
            jwks = response.json()
            
            # Index keys by kid for fast lookup
            keys = {}
            for key in jwks.get('keys', []):
                kid = key.get('kid')
                if kid:
                    keys[kid] = key
            
            logger.info(f"Fetched {len(keys)} keys from JWKS endpoint")
            return keys
        
        except requests.RequestException as e:
            logger.error(f"Failed to fetch JWKS: {e}")
            raise
    
    def _is_cache_valid(self) -> bool:
        """Check if the cache is still valid based on TTL."""
        return (time.time() - self._last_fetch) < self._cache_ttl
    
    def get_key(self, kid: str) -> Optional[Dict[str, Any]]:
        """
        Get a public key by its key ID (kid).
        
        Implements key rotation support: if kid not found and cache is stale,
        refetch once and try again.
        
        Args:
            kid: The key ID from the JWT header
            
        Returns:
            The JWK dict if found, None otherwise
        """
        with self._lock:
            # If cache is empty or expired, refresh
            if not self._keys or not self._is_cache_valid():
                self._keys = self._fetch_jwks()
                self._last_fetch = time.time()
            
            # Try to find the key
            if kid in self._keys:
                return self._keys[kid]
            
            # Key not found - might be key rotation
            # Refetch once if we haven't just fetched
            if (time.time() - self._last_fetch) > 5:  # 5 second debounce
                logger.info(f"Key {kid} not found, refetching JWKS for potential key rotation")
                self._keys = self._fetch_jwks()
                self._last_fetch = time.time()
                
                if kid in self._keys:
                    return self._keys[kid]
            
            logger.warning(f"Key {kid} not found in JWKS")
            return None
    
    def clear(self):
        """Clear the cache (useful for testing)."""
        with self._lock:
            self._keys = {}
            self._last_fetch = 0


# Global singleton instance
_jwks_cache: Optional[JWKSCache] = None


def get_jwks_cache() -> JWKSCache:
    """Get the global JWKS cache instance."""
    global _jwks_cache
    if _jwks_cache is None:
        _jwks_cache = JWKSCache(
            jwks_url=settings.KC_JWKS_URL,
            cache_ttl=settings.KC_JWKS_CACHE_TTL
        )
    return _jwks_cache
