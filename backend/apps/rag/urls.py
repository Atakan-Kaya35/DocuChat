"""
RAG URL routing.
"""
from django.urls import path

from apps.rag.views import RetrieveView, AskView

urlpatterns = [
    path('retrieve', RetrieveView.as_view(), name='rag-retrieve'),
    path('ask', AskView.as_view(), name='rag-ask'),
]
