"""
Authentication views.
"""
import logging

from django.http import JsonResponse, HttpRequest
from django.views.decorators.http import require_http_methods

from .middleware import auth_required

logger = logging.getLogger(__name__)


@require_http_methods(["GET"])
@auth_required
def me(request: HttpRequest) -> JsonResponse:
    """
    GET /api/me
    
    Returns the authenticated user's information.
    
    Response:
        {
            "id": "<sub>",
            "username": "<preferred_username>",
            "email": "<email or null>",
            "roles": ["user", "admin"]
        }
    """
    claims = request.user_claims
    
    return JsonResponse({
        'id': claims.sub,
        'username': claims.preferred_username,
        'email': claims.email,
        'roles': claims.roles
    })


@require_http_methods(["GET"])
def health(request: HttpRequest) -> JsonResponse:
    """
    GET /api/health
    
    Health check endpoint (no auth required).
    """
    return JsonResponse({'status': 'ok'})
