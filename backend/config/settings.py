"""
Django settings for DocuChat backend.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-dev-key-change-in-production')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.getenv('DEBUG', 'False').lower() in ('true', '1', 'yes')

ALLOWED_HOSTS = [
    h.strip() for h in os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')
]

# Application definition
INSTALLED_APPS = [
    'daphne',  # ASGI server for Channels
    'django.contrib.contenttypes',
    'django.contrib.auth',
    'channels',
    'rest_framework',
    'apps.authn',
    'apps.docs',
    'apps.indexing',
    'apps.rag',
    'apps.agent',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.middleware.common.CommonMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = []

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

# Database
# Using environment variable for database URL
DATABASE_URL = os.getenv('DATABASE_URL', '')
if DATABASE_URL:
    import re
    match = re.match(
        r'postgres://(?P<user>[^:]+):(?P<password>[^@]+)@(?P<host>[^:]+):(?P<port>\d+)/(?P<name>.+)',
        DATABASE_URL
    )
    if match:
        DATABASES = {
            'default': {
                'ENGINE': 'django.db.backends.postgresql',
                'NAME': match.group('name'),
                'USER': match.group('user'),
                'PASSWORD': match.group('password'),
                'HOST': match.group('host'),
                'PORT': match.group('port'),
            }
        }
    else:
        DATABASES = {
            'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': BASE_DIR / 'db.sqlite3',
            }
        }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

# Password validation (minimal for API-only backend)
AUTH_PASSWORD_VALIDATORS = []

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# =============================================================================
# Django REST Framework
# =============================================================================
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [],
    'DEFAULT_PERMISSION_CLASSES': [],
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ],
    'UNAUTHENTICATED_USER': None,
}

# =============================================================================
# Keycloak / JWT Configuration
# =============================================================================
KC_BASE_URL = os.getenv('KC_BASE_URL', 'http://keycloak:8080')
KC_REALM = os.getenv('KC_REALM', 'docuchat')
KC_AUDIENCE = os.getenv('KC_AUDIENCE', 'docuchat-frontend')
KC_ISSUER = os.getenv('KC_ISSUER', f'{KC_BASE_URL}/realms/{KC_REALM}')
KC_JWKS_URL = f'{KC_BASE_URL}/realms/{KC_REALM}/protocol/openid-connect/certs'

# External issuer for tokens issued via browser (through nginx proxy)
# Tokens from the frontend will have this issuer
KC_EXTERNAL_ISSUER = os.getenv('KC_EXTERNAL_ISSUER', 'http://localhost/realms/docuchat')

# List of valid issuers (internal + external)
KC_VALID_ISSUERS = [KC_ISSUER, KC_EXTERNAL_ISSUER]

# JWKS cache TTL in seconds (10 minutes default)
KC_JWKS_CACHE_TTL = int(os.getenv('KC_JWKS_CACHE_TTL', '600'))

# =============================================================================
# Redis / Celery
# =============================================================================
REDIS_URL = os.getenv('REDIS_URL', 'redis://redis:6379/0')
CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', REDIS_URL)

# =============================================================================
# Django Channels (WebSocket Support)
# =============================================================================
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            'hosts': [os.getenv('REDIS_URL', 'redis://redis:6379/0')],
        },
    },
}

# =============================================================================
# Ollama
# =============================================================================
OLLAMA_BASE_URL = os.getenv('OLLAMA_BASE_URL', 'http://ollama:11434')
OLLAMA_EMBED_MODEL = os.getenv('OLLAMA_EMBED_MODEL', 'nomic-embed-text')
OLLAMA_CHAT_MODEL = os.getenv('OLLAMA_CHAT_MODEL', 'gemma:7b')

# LLM Timeout settings (in seconds) - increase for slower hardware
# Default: 5 minutes for planning, 10 minutes for chat/agent
OLLAMA_PLAN_TIMEOUT = int(os.getenv('OLLAMA_PLAN_TIMEOUT', '300'))  # 5 min
OLLAMA_CHAT_TIMEOUT = int(os.getenv('OLLAMA_CHAT_TIMEOUT', '600'))  # 10 min
OLLAMA_EMBED_TIMEOUT = int(os.getenv('OLLAMA_EMBED_TIMEOUT', '120'))  # 2 min

# Query Refinement Feature (optional)
# When enabled at server level, the refine_prompt toggle in UI will work
# Set to False to disable query refinement server-wide
ENABLE_QUERY_REFINEMENT = os.getenv('ENABLE_QUERY_REFINEMENT', 'True').lower() in ('true', '1', 'yes')

# =============================================================================
# Cross-Encoder Reranker (optional)
# =============================================================================
# When enabled at server level AND request toggle is on, reranking is applied
# Set to False to disable reranking server-wide (default)
ENABLE_RERANKER = os.getenv('ENABLE_RERANKER', 'True').lower() in ('true', '1', 'yes')

# Number of candidates to retrieve from vector search for reranking
RERANK_TOP_K = int(os.getenv('RERANK_TOP_K', '20'))

# Number of candidates to keep after reranking
RERANK_KEEP_N = int(os.getenv('RERANK_KEEP_N', '8'))

# =============================================================================
# File Upload Configuration
# =============================================================================
# Root directory for uploaded files
UPLOAD_ROOT = Path(os.getenv('UPLOAD_ROOT', '/data/uploads'))

# Root directory for extracted text files (sidecar files)
EXTRACTED_ROOT = Path(os.getenv('EXTRACTED_ROOT', '/data/extracted'))

# Maximum file size in bytes (50MB default)
MAX_UPLOAD_SIZE = int(os.getenv('MAX_UPLOAD_SIZE', 50 * 1024 * 1024))

# Allowed MIME types for upload
ALLOWED_CONTENT_TYPES = [
    'application/pdf',
    'text/plain',
    'text/markdown',
    # Some systems use these for markdown
    'text/x-markdown',
]

# Allowed file extensions (used as secondary check)
ALLOWED_EXTENSIONS = ['.pdf', '.txt', '.md', '.markdown']

# =============================================================================
# Logging
# =============================================================================
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
        'json': {
            'format': '%(message)s',  # Audit logs are already JSON
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
        'audit': {
            'class': 'logging.StreamHandler',
            'formatter': 'json',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'apps.authn': {
            'handlers': ['console'],
            'level': 'DEBUG',
            'propagate': False,
        },
        'apps.rag': {
            'handlers': ['console'],
            'level': 'DEBUG',
            'propagate': False,
        },
        'audit': {
            'handlers': ['audit'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}
