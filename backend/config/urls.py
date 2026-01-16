"""
URL configuration for DocuChat backend.
"""
from django.urls import path, include

urlpatterns = [
    path('api/', include('apps.authn.urls')),
]
