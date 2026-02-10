#!/usr/bin/env python
"""Django management entrypoint for the CRM.

Usage:
    python manage_crm.py migrate
    python manage_crm.py createsuperuser
    python manage_crm.py runserver
    python manage_crm.py setup_crm  (via: python -m linkedin.management.setup_crm)
"""
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "linkedin.django_settings")
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
