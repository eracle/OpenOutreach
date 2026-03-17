#!/usr/bin/env python
"""OpenOutreach management entrypoint.

Usage:
    python manage.py              # run the daemon
    python manage.py runserver    # Django Admin at http://localhost:8000/admin/
    python manage.py migrate      # run Django migrations
    python manage.py createsuperuser
"""
import logging
import os
import sys
import warnings

# langchain-openai stores a Pydantic model in a dict-typed field, triggering
# a harmless serialization warning on every structured-output call.
warnings.filterwarnings("ignore", message="Pydantic serializer warnings")

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "linkedin.django_settings")

import django
django.setup()

from linkedin.management.setup_crm import setup_crm

logging.getLogger().handlers.clear()
logging.basicConfig(
    level=logging.DEBUG,
    format="%(message)s",
)

# Suppress noisy third-party loggers
for _name in ("urllib3", "httpx", "langchain", "openai", "playwright",
              "httpcore", "fastembed", "huggingface_hub", "filelock"):
    logging.getLogger(_name).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def _run_daemon():
    from linkedin.api.newsletter import ensure_newsletter_subscription
    from linkedin.daemon import run_daemon
    from linkedin.db.urls import public_id_to_url
    from linkedin.setup.self_profile import ensure_self_profile
    from linkedin.setup.gdpr import apply_gdpr_newsletter_override
    from linkedin.onboarding import ensure_onboarding
    from linkedin.browser.registry import get_or_create_session

    ensure_onboarding()

    from linkedin.conf import LLM_API_KEY, get_first_active_profile_handle

    if not LLM_API_KEY:
        logger.error("LLM_API_KEY is required. Set it in .env or environment.")
        sys.exit(1)

    handle = get_first_active_profile_handle()
    if handle is None:
        logger.error("No active LinkedIn profiles found.")
        sys.exit(1)

    session = get_or_create_session(handle=handle)

    # Set default campaign (first non-freemium, or first available) for startup tasks
    first_campaign = session.campaigns.filter(is_freemium=False).first() or session.campaigns.first()
    if first_campaign is None:
        logger.error("No campaigns found for this user.")
        sys.exit(1)
    session.campaign = first_campaign

    session.ensure_browser()
    profile = ensure_self_profile(session)

    if not session.linkedin_profile.newsletter_processed:
        country_code = profile.get("country_code") if profile else None
        apply_gdpr_newsletter_override(session, country_code)
        linkedin_url = public_id_to_url(profile["public_identifier"]) if profile else None
        ensure_newsletter_subscription(session, linkedin_url=linkedin_url)
        session.linkedin_profile.newsletter_processed = True
        session.linkedin_profile.save(update_fields=["newsletter_processed"])

    run_daemon(session)


def _ensure_db():
    from django.core.management import call_command
    call_command("migrate", "--no-input", verbosity=0)
    setup_crm()


if __name__ == "__main__":
    if len(sys.argv) == 1:
        # No arguments → run the daemon
        _ensure_db()
        _run_daemon()
    else:
        # Auto-migrate before starting the admin server
        if sys.argv[1] == "runserver":
            _ensure_db()
        # Django management command (runserver, migrate, createsuperuser, etc.)
        from django.core.management import execute_from_command_line
        execute_from_command_line(sys.argv)
