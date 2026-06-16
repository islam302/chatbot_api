"""Django settings for the ChatBot REST API."""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: list[str] | None = None) -> list[str]:
    value = os.getenv(name)
    if not value:
        return list(default or [])
    return [item.strip() for item in value.split(",") if item.strip()]


SECRET_KEY = os.getenv("SECRET_KEY", "django-insecure-change-me")
DEBUG = _env_bool("DEBUG", default=False)

ALLOWED_HOSTS = _env_list(
    "ALLOWED_HOSTS",
    default=["localhost", "127.0.0.1"],
)

CORS_ALLOWED_ORIGINS = _env_list(
    "CORS_ALLOWED_ORIGINS",
    default=["http://localhost:3000", "http://127.0.0.1:3000"],
)
CORS_ALLOW_CREDENTIALS = True


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third party
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "drf_spectacular",
    "django_filters",
    # Local
    "Authentication",
    "knowledge",
    "WhatsApp",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "Conf.urls"
WSGI_APPLICATION = "Conf.wsgi.application"
ASGI_APPLICATION = "Conf.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


_db_url = os.getenv("DATABASE_URL")
if _db_url:
    # Lightweight DATABASE_URL parser (postgres://user:pass@host:port/name)
    from urllib.parse import urlparse

    parsed = urlparse(_db_url)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": parsed.path.lstrip("/"),
            "USER": parsed.username or "",
            "PASSWORD": parsed.password or "",
            "HOST": parsed.hostname or "",
            "PORT": str(parsed.port) if parsed.port else "",
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }


AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

MEDIA_URL = "/media/"
MEDIA_ROOT = Path(os.getenv("MEDIA_ROOT", BASE_DIR / "media"))

AUTH_USER_MODEL = "Authentication.User"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
        # Accept text/plain bodies as JSON too (tolerant of clients that omit
        # the Content-Type: application/json header).
        "Conf.parsers.PlainTextJSONParser",
        "rest_framework.parsers.MultiPartParser",
        "rest_framework.parsers.FormParser",
    ],
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(days=3),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=30),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": SECRET_KEY,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "ChatBot API",
    "DESCRIPTION": "REST API for the ChatBot platform with WhatsApp integration.",
    "VERSION": "2.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": "/api/v1/",
    "TAGS": [
        {"name": "Authentication", "description": "Login, refresh, register"},
        {"name": "Questions", "description": "Fixed and dynamic Q&A"},
        {"name": "Tree", "description": "Hierarchical guided questions"},
        {"name": "Documents", "description": "RAG document storage"},
        {"name": "Chat", "description": "Chat endpoints"},
        {"name": "WhatsApp", "description": "WhatsApp integration"},
        {"name": "Analytics", "description": "Aggregated metrics"},
    ],
}


SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"


CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "chatbot-api-cache",
    }
}


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {name} {message}",
            "style": "{",
        },
        "simple": {"format": "{levelname} {message}", "style": "{"},
    },
    "handlers": {
        "console": {
            "level": "INFO",
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "knowledge": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "WhatsApp": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}


# Application-specific knobs
RAG_SIMILARITY_THRESHOLD = float(os.getenv("RAG_SIMILARITY_THRESHOLD", "0.5"))

# Max size (MB) for a single uploaded knowledge document. Ingestion runs
# synchronously (parse -> chunk -> embed), so this caps request time and cost.
MAX_UPLOAD_SIZE_MB = float(os.getenv("MAX_UPLOAD_SIZE_MB", "20"))

# --- Retrieval (RAG) tuning -------------------------------------------------
# Final number of chunks passed to the LLM.
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "6"))
# Candidate pool pulled before re-ranking (fetch_k >= top_k).
RAG_FETCH_K = int(os.getenv("RAG_FETCH_K", "40"))
# Diversify results with Maximal Marginal Relevance (avoids near-duplicate chunks).
RAG_USE_MMR = _env_bool("RAG_USE_MMR", default=True)
# MMR trade-off: 1.0 = pure relevance, 0.0 = pure diversity.
RAG_MMR_LAMBDA = float(os.getenv("RAG_MMR_LAMBDA", "0.6"))
# Rows scanned per batch by the numpy backend (bounds peak memory per query).
RAG_SCAN_BATCH = int(os.getenv("RAG_SCAN_BATCH", "2000"))
# Vector search backend: "numpy" (works everywhere) or "pgvector" (Postgres + index).
RAG_VECTOR_BACKEND = os.getenv("RAG_VECTOR_BACKEND", "numpy").lower()

# Max conversation turns kept in a chat `history` (1 turn = user + assistant).
CHAT_MAX_HISTORY_TURNS = int(os.getenv("CHAT_MAX_HISTORY_TURNS", "10"))

# --- Multi-tenancy: per-tenant default quotas --------------------------------
# A "tenant" is a User. These are the DEFAULTS; any tenant can be given its own
# limits via a TenantQuota row (null fields on that row fall back to these).
TENANT_MAX_DOCUMENTS = int(os.getenv("TENANT_MAX_DOCUMENTS", "100"))
TENANT_MAX_TOTAL_MB = float(os.getenv("TENANT_MAX_TOTAL_MB", "200"))
# Sustained chat rate limit per tenant (requests counted in a 60s window).
TENANT_MAX_REQUESTS_PER_MIN = int(os.getenv("TENANT_MAX_REQUESTS_PER_MIN", "60"))
# Monthly LLM token budget per tenant (input+output). 0 = unlimited.
TENANT_MONTHLY_TOKEN_CAP = int(os.getenv("TENANT_MONTHLY_TOKEN_CAP", "0"))

# --- LLM / embedding pricing (USD per 1,000,000 tokens) ----------------------
# Used only to estimate per-request cost for usage analytics. Unknown models
# cost 0 (so analytics never blocks; it just under-reports).
LLM_PRICING = {
    "gpt-4o": {"input": 2.5, "output": 10.0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.6},
    "gpt-4.1": {"input": 2.0, "output": 8.0},
    "gpt-4.1-mini": {"input": 0.4, "output": 1.6},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-opus-4-8": {"input": 5.0, "output": 25.0},
}
EMBEDDING_PRICING = {
    "text-embedding-3-large": 0.13,
    "text-embedding-3-small": 0.02,
}

# --- Ingestion execution ----------------------------------------------------
# How document ingestion runs:
#   "sync"   - in-request (default; simplest)
#   "thread" - daemon thread, returns immediately (single server, no infra)
#   "celery" - enqueue to a Celery worker (production; needs Redis + worker)
INGESTION_MODE = os.getenv("INGESTION_MODE", "sync").lower()

# --- Celery / Redis ---------------------------------------------------------
# In Docker these point at the `redis` service; locally default to localhost.
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", REDIS_URL)
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_TIME_LIMIT = 60 * 30  # 30 min hard cap per ingestion task
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

# --- Production hardening (behind the nginx reverse proxy) -------------------
# Trust the proxy's X-Forwarded-Proto so Django knows the request was HTTPS.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True
# Domains allowed to POST (admin, browsable API). Set to your https domain(s).
CSRF_TRUSTED_ORIGINS = _env_list("CSRF_TRUSTED_ORIGINS", default=[])

# Extra HTTPS-only protections, enabled when DEBUG is off.
if not DEBUG:
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", default=True)
    CSRF_COOKIE_SECURE = _env_bool("CSRF_COOKIE_SECURE", default=True)
    SECURE_SSL_REDIRECT = _env_bool("SECURE_SSL_REDIRECT", default=False)
    SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "0"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = _env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", default=True)
    SECURE_HSTS_PRELOAD = _env_bool("SECURE_HSTS_PRELOAD", default=True)

# --- Redis cache (used when REDIS_URL points at a reachable Redis) -----------
# Overrides the in-memory cache so rate-limit counters etc. are shared across
# gunicorn workers. Falls back silently to locmem if you don't set USE_REDIS_CACHE.
if _env_bool("USE_REDIS_CACHE", default=False):
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
        }
    }
