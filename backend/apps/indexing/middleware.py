"""
WebSocket JWT Authentication Middleware for Django Channels.

Authenticates WebSocket connections using JWT token from query string.
"""
import logging
from urllib.parse import parse_qs
from typing import Optional

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.conf import settings

from apps.authn.jwt import JWTValidator, JWTValidationError

logger = logging.getLogger(__name__)


class JWTAuthMiddleware(BaseMiddleware):
    """
    Middleware that authenticates WebSocket connections using JWT.
    
    The token is passed in the query string as ?token=<jwt>
    
    On success, adds 'user' dict to scope with:
    - id: user ID (sub claim)
    - username: preferred_username
    - roles: list of realm roles
    """
    
    async def __call__(self, scope, receive, send):
        # Only handle WebSocket connections
        if scope["type"] != "websocket":
            return await super().__call__(scope, receive, send)
        
        # Extract token from query string
        query_string = scope.get("query_string", b"").decode()
        query_params = parse_qs(query_string)
        token_list = query_params.get("token", [])
        
        if not token_list:
            logger.warning("WebSocket connection rejected: no token provided")
            scope["user"] = None
            return await super().__call__(scope, receive, send)
        
        token = token_list[0]
        
        # Validate token
        user = await self._validate_token(token)
        
        if user:
            logger.info(f"WebSocket authenticated for user {user['id']}")
            scope["user"] = user
        else:
            logger.warning("WebSocket connection rejected: invalid token")
            scope["user"] = None
        
        return await super().__call__(scope, receive, send)
    
    @database_sync_to_async
    def _validate_token(self, token: str) -> Optional[dict]:
        """
        Validate JWT token and extract user info.
        
        Returns user dict on success, None on failure.
        """
        try:
            validator = JWTValidator()
            payload = validator.validate(token)
            
            return {
                'id': payload.get('sub'),
                'username': payload.get('preferred_username', 'unknown'),
                'roles': payload.get('realm_access', {}).get('roles', []),
            }
        except JWTValidationError as e:
            logger.warning(f"JWT validation failed: {e}")
            return None
        except Exception as e:
            logger.exception(f"Unexpected error validating JWT: {e}")
            return None
