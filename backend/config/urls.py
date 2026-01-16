"""
URL configuration for DocuChat backend.
"""
from django.urls import path, include
from django.http import JsonResponse

from apps.agent.health import healthz, readyz


def health_check(request):
    """Simple health check endpoint for Docker healthcheck (legacy)."""
    return JsonResponse({'status': 'ok'})


urlpatterns = [
    # Health check endpoints (no auth)
    path('healthz', healthz, name='healthz'),
    path('readyz', readyz, name='readyz'),
    path('api/health/', health_check, name='health_check'),  # Legacy
    
    # API routes
    path('api/', include('apps.authn.urls')),
    path('api/docs/', include('apps.docs.urls')),
    path('api/rag/', include('apps.rag.urls')),
]
