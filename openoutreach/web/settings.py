"""Opt-in web settings; the CLI keeps its original lightweight settings."""
import secrets
from pathlib import Path

from openoutreach.settings import *  # noqa: F403

SECRET_KEY = secrets.token_urlsafe(48)
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "[::1]"]
ROOT_URLCONF = "openoutreach.web.urls"
MIDDLEWARE = [
    "openoutreach.web.middleware.LocalOnlyMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [Path(__file__).parent / "templates"],
    "APP_DIRS": False,
    "OPTIONS": {"context_processors": ["django.template.context_processors.csrf"]},
}]
CSRF_COOKIE_SAMESITE = "Strict"
X_FRAME_OPTIONS = "DENY"
DATA_UPLOAD_MAX_MEMORY_SIZE = 128 * 1024
