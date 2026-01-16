"""
Authentication middleware for JWT-protected endpoints.
"""
import logging
from typing import Optional, Callable
from functools import wraps

from django.http import JsonResponse, HttpRequest

from .jwt_validator import validate_token, TokenClaims, JWTValidationError

logger = logging.getLogger(__name__)


def get_token_from_request(request: HttpRequest) -> Optional[str]:
    """
    Extract Bearer token from Authorization header.
    
    Args:
        request: The Django HTTP request
        
    Returns:
        The token string if found, None otherwise
    """
    auth_header = request.META.get('HTTP_AUTHORIZATION', '')
    
    if not auth_header:
        return None
    
    parts = auth_header.split()
    
    if len(parts) != 2 or parts[0].lower() != 'bearer':
        return None
    
    return parts[1]


def auth_required(view_func: Callable) -> Callable:
    """
    Decorator that requires a valid JWT token.
    
    Validates the token and attaches the claims to request.user_claims.
    
    Usage:
        @auth_required
        def my_view(request):
            user_id = request.user_claims.sub
            roles = request.user_claims.roles
            ...
    """
    @wraps(view_func)
    def wrapper(request: HttpRequest, *args, **kwargs):
        token = get_token_from_request(request)
        
        if not token:
            return JsonResponse(
                {'error': 'Authorization header missing or invalid'},
                status=401
            )
        
        try:
            claims = validate_token(token)
            request.user_claims = claims
            logger.debug(
                f"Authenticated user: {claims.preferred_username} "
                f"(sub={claims.sub}, roles={claims.roles})"
            )
            return view_func(request, *args, **kwargs)
        
        except JWTValidationError as e:
            logger.warning(f"JWT validation failed: {e}")
            return JsonResponse(
                {'error': str(e)},
                status=401
            )
    
    return wrapper


def role_required(*required_roles: str) -> Callable:
    """
    Decorator that requires specific roles in addition to authentication.
    
    Must be used after @auth_required.
    
    Usage:
        @auth_required
        @role_required('admin')
        def admin_only_view(request):
            ...
    """
    def decorator(view_func: Callable) -> Callable:
        @wraps(view_func)
        def wrapper(request: HttpRequest, *args, **kwargs):
            claims: Optional[TokenClaims] = getattr(request, 'user_claims', None)
            
            if not claims:
                return JsonResponse(
                    {'error': 'Authentication required'},
                    status=401
                )
            
            user_roles = set(claims.roles)
            required = set(required_roles)
            
            if not required.intersection(user_roles):
                logger.warning(
                    f"User {claims.preferred_username} lacks required roles. "
                    f"Has: {claims.roles}, Needs one of: {list(required_roles)}"
                )
                return JsonResponse(
                    {'error': 'Insufficient permissions'},
                    status=403
                )
            
            return view_func(request, *args, **kwargs)
        
        return wrapper
    
    return decorator
