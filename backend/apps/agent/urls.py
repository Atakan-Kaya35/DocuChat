"""
URL configuration for agent app.
"""
from django.urls import path

from apps.agent.views import AgentRunView, AgentStreamView

urlpatterns = [
    path('run', AgentRunView.as_view(), name='agent_run'),
    path('stream', AgentStreamView.as_view(), name='agent_stream'),
]
