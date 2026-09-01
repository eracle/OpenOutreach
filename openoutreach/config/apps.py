# openoutreach/config/apps.py
from django.apps import AppConfig


class ConfigAppConfig(AppConfig):
    name = "openoutreach.config"
    # Namespaced like the children's, for the same reason: three app sets share one
    # registry and a bare `config` is exactly the label somebody else will want.
    label = "openoutreach_config"
    default_auto_field = "django.db.models.BigAutoField"
