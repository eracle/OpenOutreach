# openoutreach/legacy/apps.py
from django.apps import AppConfig


class LegacyConfig(AppConfig):
    # Model-less app retained only to anchor migration history: its migrations
    # created the engine models (SiteConfig/Campaign/Task, since moved to `core`)
    # and the old channel models (deleted in 0012). Kept installed so existing
    # installs keep a valid, forward-only migration graph. The `legacy` label is
    # a stable identifier; rename it only alongside a `django_migrations` update.
    name = "openoutreach.legacy"
    label = "legacy"
    default_auto_field = "django.db.models.BigAutoField"
