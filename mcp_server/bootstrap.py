"""Django bootstrap for MCP tools."""
from __future__ import annotations

import os


def setup_django() -> None:
    """Initialize Django once for tool handlers."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "linkedin.django_settings")

    import django

    django.setup()

