#!/usr/bin/env python
"""Django management entrypoint.

Usage:
    python manage.py rundaemon                     # run the daemon (interactive onboarding)
    python manage.py rundaemon --onboard config.json  # non-interactive onboarding
    python manage.py runserver                     # Django Admin at http://localhost:8000/admin/
    python manage.py migrate                       # run Django migrations
    python manage.py createsuperuser
"""
import os
import sys
import warnings

# langchain-openai stores a Pydantic model in a dict-typed field, triggering
# a harmless serialization warning on every structured-output call.
warnings.filterwarnings("ignore", message="Pydantic serializer warnings")

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "linkedin.django_settings")


if __name__ == "__main__":
    from django.core.management import execute_from_command_line

    # No subcommand (or first arg is a flag) → default to rundaemon.
    if len(sys.argv) == 1 or sys.argv[1].startswith("-"):
        sys.argv = [sys.argv[0], "rundaemon"] + sys.argv[1:]

    execute_from_command_line(sys.argv)
