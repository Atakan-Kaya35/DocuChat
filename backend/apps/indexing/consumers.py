"""
WebSocket Consumer for Indexing Progress Events.

Clients connect to /ws/indexing?token=<jwt> to receive real-time
progress updates for their document indexing jobs.
"""
import json
import logging
from typing import Optional

from channels.generic.websocket import AsyncJsonWebsocketConsumer
from asgiref.sync import sync_to_async

from apps.indexing.events import IndexProgressEvent

logger = logging.getLogger(__name__)


class IndexingProgressConsumer(AsyncJsonWebsocketConsumer):
    """
    WebSocket consumer that:
    1. Authenticates via JWT (from middleware)
    2. Joins a user-specific channel group
    3. Receives events from Redis and forwards to the client
    4. Handles disconnect cleanly
    
    Clients receive IndexProgressEvent messages for their documents.
    """
    
    async def connect(self):
        """Handle new WebSocket connection."""
        # Get user from scope (set by JWTAuthMiddleware)
        self.user = self.scope.get("user")
        
        if not self.user:
            # Reject unauthenticated connections
            logger.warning("Rejecting unauthenticated WebSocket connection")
            await self.close(code=4001)
            return
        
        self.user_id = self.user["id"]
        self.group_name = f"user_{self.user_id}"
        
        # Join user-specific group
        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )
        
        await self.accept()
        
        logger.info(f"WebSocket connected for user {self.user_id}")
        
        # Send a welcome message
        await self.send_json({
            "type": "connected",
            "message": "Connected to indexing progress stream",
            "userId": self.user_id
        })
    
    async def disconnect(self, close_code):
        """Handle WebSocket disconnect."""
        if hasattr(self, 'group_name'):
            # Leave user group
            await self.channel_layer.group_discard(
                self.group_name,
                self.channel_name
            )
            logger.info(f"WebSocket disconnected for user {self.user_id} (code={close_code})")
    
    async def receive_json(self, content):
        """
        Handle messages from client.
        
        Currently just echoes back for debugging.
        Could be extended for client->server commands.
        """
        logger.debug(f"Received from client: {content}")
        
        # Echo back for now (useful for ping/pong)
        if content.get("type") == "ping":
            await self.send_json({"type": "pong"})
    
    async def index_progress(self, event):
        """
        Handle index_progress events from channel layer.
        
        These are sent when the worker publishes progress updates.
        """
        # Forward the event data to the WebSocket client with type wrapper
        await self.send_json({
            "type": "index_progress",
            "data": event["data"]
        })
    
    async def index_complete(self, event):
        """Handle index_complete events from channel layer."""
        await self.send_json({
            "type": "index_complete",
            "data": event["data"]
        })
    
    async def index_failed(self, event):
        """Handle index_failed events from channel layer."""
        await self.send_json({
            "type": "index_failed",
            "data": event["data"]
        })
