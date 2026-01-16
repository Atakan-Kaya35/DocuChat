"""
Health check endpoints for Kubernetes/Docker probes.

- /healthz - Liveness (is process running?)
- /readyz - Readiness (can we serve traffic?)
"""
import logging
from datetime import datetime, timezone

import redis
import httpx
from django.conf import settings
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)


def get_timestamp() -> str:
    """Get current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat()


@csrf_exempt
@require_GET
def healthz(request):
    """
    Liveness probe endpoint.
    
    Returns 200 if the Django process is running.
    Does NOT check dependencies - that's for readiness.
    """
    return JsonResponse({
        'status': 'healthy',
        'timestamp': get_timestamp()
    })


def check_postgres() -> tuple[str, bool]:
    """Check PostgreSQL connectivity."""
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
        return 'ok', True
    except Exception as e:
        logger.error(f"Postgres health check failed: {e}")
        return f'error: {str(e)[:50]}', False


def check_redis() -> tuple[str, bool]:
    """Check Redis connectivity."""
    try:
        redis_url = getattr(settings, 'REDIS_URL', 'redis://redis:6379/0')
        client = redis.from_url(redis_url, socket_timeout=3)
        client.ping()
        return 'ok', True
    except Exception as e:
        logger.error(f"Redis health check failed: {e}")
        return f'error: {str(e)[:50]}', False


def check_ollama() -> tuple[str, bool]:
    """
    Check Ollama connectivity (optional, degrades gracefully).
    
    Ollama being down shouldn't prevent serving existing content.
    """
    try:
        ollama_url = getattr(settings, 'OLLAMA_BASE_URL', 'http://ollama:11434')
        with httpx.Client(timeout=5.0) as client:
            response = client.get(f'{ollama_url}/api/version')
            if response.status_code == 200:
                return 'ok', True
            return f'status: {response.status_code}', True  # Still "ok" - Ollama is reachable
    except Exception as e:
        logger.warning(f"Ollama health check failed: {e}")
        # Ollama failure is not critical for readiness
        return f'degraded: {str(e)[:30]}', True


@csrf_exempt
@require_GET
def readyz(request):
    """
    Readiness probe endpoint.
    
    Returns 200 only if all critical dependencies are reachable.
    Used to determine if the pod should receive traffic.
    """
    checks = {}
    all_ok = True
    
    # Check PostgreSQL (critical)
    status, ok = check_postgres()
    checks['postgres'] = status
    if not ok:
        all_ok = False
    
    # Check Redis (critical for rate limiting and channels)
    status, ok = check_redis()
    checks['redis'] = status
    if not ok:
        all_ok = False
    
    # Check Ollama (optional - doesn't block readiness)
    status, _ = check_ollama()
    checks['ollama'] = status
    
    response_data = {
        'status': 'ready' if all_ok else 'not_ready',
        'timestamp': get_timestamp(),
        'checks': checks
    }
    
    return JsonResponse(response_data, status=200 if all_ok else 503)
