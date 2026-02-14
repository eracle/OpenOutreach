# linkedin/django_settings.py
"""
Minimal Django settings for using DjangoCRM's ORM + admin.
"""
import os
import sys
from datetime import datetime as dt
from pathlib import Path

# Playwright's sync API runs inside an async event loop, which triggers
# Django's async-safety check. We only use the ORM synchronously, so this is safe.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

from django.utils.translation import gettext_lazy as _

# Import DjangoCRM's default settings (columns, IMAP, VOIP, etc.)
from crm.settings import *       # noqa: F401,F403
from common.settings import *    # noqa: F401,F403
from tasks.settings import *     # noqa: F401,F403
from voip.settings import *      # noqa: F401,F403

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "assets" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

BASE_DIR = ROOT_DIR

SECRET_KEY = "openoutreach-local-dev-key-change-in-production"

DEBUG = True

ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.sites",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "crm.apps.CrmConfig",
    "massmail.apps.MassmailConfig",
    "analytics.apps.AnalyticsConfig",
    "help",
    "tasks.apps.TasksConfig",
    "chat.apps.ChatConfig",
    "voip",
    "common.apps.CommonConfig",
    "settings",
    "quality",
    "linkedin",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "common.utils.admin_redirect_middleware.AdminRedirectMiddleware",
    "common.utils.usermiddleware.UserMiddleware",
]

ROOT_URLCONF = "linkedin.urls"

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

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(DATA_DIR / "crm.db"),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SITE_ID = 1

STATIC_URL = "/static/"
STATIC_ROOT = ROOT_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = DATA_DIR / "media"

LOGIN_URL = "/admin/login/"

# DjangoCRM settings
SECRET_CRM_PREFIX = "crm"
SECRET_ADMIN_PREFIX = "admin"
SECRET_LOGIN_PREFIX = "login"

CRM_IP = "127.0.0.1"
CRM_REPLY_TO = []
NOT_ALLOWED_EMAILS = []

APP_ON_INDEX_PAGE = ["tasks", "crm"]
MODEL_ON_INDEX_PAGE = {
    "crm": {"app_model_list": ["Deal", "Lead", "Company"]},
    "tasks": {"app_model_list": ["Task", "Memo"]},
}

VAT = 0
GEOIP = False
SHOW_USER_CURRENT_TIME_ZONE = False
NO_NAME_STR = _("Untitled")
LOAD_EXCHANGE_RATE = False
LOADING_EXCHANGE_RATE_TIME = "6:30"
LOAD_RATE_BACKEND = ""
MARK_PAYMENTS_THROUGH_REP = False
MAILING = False

SITE_TITLE = "OpenOutreach CRM"
ADMIN_HEADER = "OpenOutreach Admin"
ADMIN_TITLE = "OpenOutreach CRM Admin"
INDEX_TITLE = _("Main Menu")
COPYRIGHT_STRING = f"OpenOutreach CRM {dt.now().year}"
PROJECT_NAME = "OpenOutreach"
PROJECT_SITE = ""

CLIENT_ID = ""
CLIENT_SECRET = ""
REDIRECT_URI = ""
GOOGLE_RECAPTCHA_SITE_KEY = ""
GOOGLE_RECAPTCHA_SECRET_KEY = ""

NAME_PREFIXES = [
    "Mr.", "Mrs.", "Ms.", "Miss", "Mx.", "Dr.", "Prof.",
]

DEFAULT_FROM_EMAIL = "noreply@localhost"
EMAIL_SUBJECT_PREFIX = "CRM: "

LANGUAGE_CODE = "en"
LANGUAGES = [("en", "English")]
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

TESTING = sys.argv[1:2] == ["test"]
