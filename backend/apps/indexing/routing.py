"""
WebSocket URL routing for indexing app.
"""
from django.urls import re_path

from apps.indexing.consumers import IndexingProgressConsumer

websocket_urlpatterns = [
    re_path(r"ws/indexing/?$", IndexingProgressConsumer.as_asgi()),
]
