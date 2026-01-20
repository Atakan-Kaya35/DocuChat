"""
RAG URL routing.
"""
from django.urls import path

from apps.rag.views import RetrieveView, RewriteView, AskView

urlpatterns = [
    path('retrieve', RetrieveView.as_view(), name='rag-retrieve'),
    path('rewrite', RewriteView.as_view(), name='rag-rewrite'),
    path('ask', AskView.as_view(), name='rag-ask'),
]
