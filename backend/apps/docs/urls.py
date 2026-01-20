"""
URL configuration for the docs app.
"""
from django.urls import path
from . import views

app_name = 'docs'

urlpatterns = [
    path('upload', views.upload_document, name='upload'),
    path('', views.list_documents, name='list'),
    path('<uuid:document_id>', views.get_document, name='detail'),
    path('<uuid:document_id>/delete', views.delete_document, name='delete'),
    path('<uuid:document_id>/chunks/<int:chunk_index>', views.get_chunk, name='chunk'),
]

