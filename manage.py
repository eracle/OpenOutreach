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

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "linkedin.django_settings")

import django
django.setup()

from linkedin.management.setup_crm import setup_crm

logging.getLogger().handlers.clear()
logging.basicConfig(
    level=5,
    format="%(message)s",
)

# Suppress noisy third-party loggers
for _name in ("urllib3", "httpx", "langchain", "openai", "dbt", "playwright",
              "httpcore", "fastembed", "huggingface_hub"):
    logging.getLogger(_name).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


ME_URL = "https://www.linkedin.com/in/me/"


def ensure_self_profile(session):
    """Discover the logged-in user's own profile via Voyager API and mark it disqualified.

    Creates a disqualified lead for the real profile URL and a ``/in/me/`` sentinel.
    On subsequent runs the sentinel is detected and the stored profile is
    returned from the CRM.

    Returns the parsed profile dict on first run, or ``None`` on subsequent
    runs (the GDPR check is guarded by its own marker file).
    """
    from crm.models import Lead

    from linkedin.api.client import PlaywrightLinkedinAPI
    from linkedin.db.crm_profiles import (
        create_enriched_lead,
        disqualify_lead,
        public_id_to_url,
    )

    # Sentinel check — already ran once
    if Lead.objects.filter(website=ME_URL).exists():
        logger.debug("Self-profile already discovered (sentinel exists)")
        return None

    api = PlaywrightLinkedinAPI(session=session)
    profile, data = api.get_profile(public_identifier="me")

    if not profile:
        logger.warning("Could not fetch own profile via Voyager API")
        return None

    real_id = profile["public_identifier"]
    real_url = public_id_to_url(real_id)

    # Save and disqualify the real profile
    create_enriched_lead(session, real_url, profile, data)
    disqualify_lead(session, real_id, reason="Own profile")

    # Save the /in/me/ sentinel as disqualified
    dept = session.campaign.department
    Lead.objects.get_or_create(
        website=ME_URL,
        defaults={
            "owner": session.django_user,
            "department": dept,
            "disqualified": True,
        },
    )

    logger.info("Self-profile discovered: %s", real_url)
    return profile


def _run_daemon():
    from linkedin.api.emails import ensure_newsletter_subscription
    from linkedin.daemon import run_daemon
    from linkedin.gdpr import apply_gdpr_newsletter_override
    from linkedin.onboarding import ensure_onboarding
    from linkedin.sessions.registry import get_session

    ensure_onboarding()

    from linkedin.conf import COOKIES_DIR, LLM_API_KEY, get_first_active_profile_handle

    if not LLM_API_KEY:
        logger.error("LLM_API_KEY is required. Set it in .env or environment.")
        sys.exit(1)

    handle = get_first_active_profile_handle()
    if handle is None:
        logger.error("No active LinkedIn profiles found.")
        sys.exit(1)

    session = get_session(handle=handle)

    # Set default campaign (first non-promo, or first available) for startup tasks
    first_campaign = session.campaigns.filter(is_promo=False).first() or session.campaigns.first()
    if first_campaign is None:
        logger.error("No campaigns found for this user.")
        sys.exit(1)
    session.campaign = first_campaign

    # Ensure pipeline exists for this campaign's department (may differ from default)
    from linkedin.management.setup_crm import ensure_campaign_pipeline
    ensure_campaign_pipeline(first_campaign.department)

    session.ensure_browser()
    profile = ensure_self_profile(session)

    newsletter_marker = COOKIES_DIR / f".{session.handle}_newsletter_processed"
    if not newsletter_marker.exists():
        country_code = profile.get("country_code") if profile else None
        apply_gdpr_newsletter_override(session, country_code)
        ensure_newsletter_subscription(session)
        newsletter_marker.touch()

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
        # Django management command (runserver, migrate, createsuperuser, etc.)
        from django.core.management import execute_from_command_line
        execute_from_command_line(sys.argv)
