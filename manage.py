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
    level=logging.DEBUG,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)

# Suppress noisy third-party loggers
for _name in ("urllib3", "httpx", "langchain", "dbt", "playwright", "httpcore",
              "fastembed", "huggingface_hub"):
    logging.getLogger(_name).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


ME_URL = "https://www.linkedin.com/in/me/"


def ensure_self_profile(session):
    """Discover the logged-in user's own profile via Voyager API and mark it IGNORED.

    Creates two IGNORED leads: the real profile URL and a ``/in/me/`` sentinel.
    On subsequent runs the sentinel is detected and the stored profile is
    returned from the CRM.

    Returns the parsed profile dict on first run, or ``None`` on subsequent
    runs (the GDPR check is guarded by its own marker file).
    """
    from crm.models import Lead

    from linkedin.api.client import PlaywrightLinkedinAPI
    from linkedin.db.crm_profiles import (
        add_profile_urls,
        public_id_to_url,
        save_scraped_profile,
        set_profile_state,
    )
    from linkedin.navigation.enums import ProfileState

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

    # Save and mark the real profile as IGNORED
    add_profile_urls(session, [real_url])
    save_scraped_profile(session, real_url, profile, data)
    set_profile_state(session, real_id, ProfileState.IGNORED.value, reason="Own profile")

    # Save the /in/me/ sentinel as IGNORED
    add_profile_urls(session, [ME_URL])
    set_profile_state(session, "me", ProfileState.IGNORED.value, reason="Own profile sentinel")

    logger.info("Self-profile discovered: %s", real_url)
    return profile


def _run_daemon():
    from linkedin.api.emails import ensure_newsletter_subscription
    from linkedin.conf import COOKIES_DIR, get_first_active_account
    from linkedin.daemon import run_daemon
    from linkedin.gdpr import apply_gdpr_newsletter_override
    from linkedin.onboarding import ensure_onboarding
    from linkedin.sessions.registry import get_session

    ensure_onboarding()

    handle = get_first_active_account()
    if handle is None:
        logger.error("No active accounts found.")
        sys.exit(1)

    session = get_session(handle=handle)
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
