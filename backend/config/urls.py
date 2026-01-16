"""
URL configuration for DocuChat backend.
"""
from django.urls import path, include
from django.http import JsonResponse


def health_check(request):
    """Simple health check endpoint for Docker healthcheck."""
    return JsonResponse({'status': 'ok'})


urlpatterns = [
    path('api/health/', health_check, name='health_check'),
    path('api/', include('apps.authn.urls')),
    path('api/docs/', include('apps.docs.urls')),
]
