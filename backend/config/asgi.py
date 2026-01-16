"""
ASGI config for DocuChat backend.

Handles both HTTP and WebSocket connections.
"""
import os

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# Initialize Django ASGI application early to ensure the AppRegistry
# is populated before importing code that may import ORM models.
django_asgi_app = get_asgi_application()

# Import after Django setup
from apps.indexing.routing import websocket_urlpatterns
from apps.indexing.middleware import JWTAuthMiddleware

application = ProtocolTypeRouter({
    # HTTP requests go to Django
    "http": django_asgi_app,
    
    # WebSocket connections go through JWT auth then to our consumers
    "websocket": AllowedHostsOriginValidator(
        JWTAuthMiddleware(
            URLRouter(websocket_urlpatterns)
        )
    ),
})
