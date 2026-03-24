# linkedin/browser/registry.py
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_sessions: dict[int, "AccountSession"] = {}


def get_or_create_session(linkedin_profile) -> "AccountSession":
    from linkedin.browser.session import AccountSession

    pk = linkedin_profile.pk
    if pk not in _sessions:
        _sessions[pk] = AccountSession(linkedin_profile)
        logger.debug("Created new account session for %s", linkedin_profile)
    return _sessions[pk]


def get_first_active_profile():
    """Return the first active LinkedInProfile, or None."""
    from linkedin.models import LinkedInProfile

    return LinkedInProfile.objects.filter(active=True).select_related("user").first()


def resolve_profile(username: str | None = None):
    """Resolve a LinkedInProfile from an optional username, falling back to first active."""
    if username:
        from linkedin.models import LinkedInProfile

        return LinkedInProfile.objects.select_related("user").filter(
            user__username=username,
        ).first()
    return get_first_active_profile()


def cli_parser(description: str):
    """Bootstrap Django and return an ArgumentParser with ``--handle``.

    Call from ``if __name__ == "__main__"`` blocks. Sets up Django,
    configures logging, and returns a parser with ``--handle`` pre-added.
    After adding extra arguments, call ``cli_session(args)`` to get the session.
    """
    import argparse
    import os

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "linkedin.django_settings")

    import django
    django.setup()

    logging.basicConfig(level=logging.DEBUG, format="[%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--handle", default=None, help="Django username (default: first active profile)")
    return parser


def cli_session(args) -> "AccountSession":
    """Resolve profile from parsed args, create session, set default campaign."""
    linkedin_profile = resolve_profile(args.handle)
    if not linkedin_profile:
        print("No active LinkedInProfile found.")
        raise SystemExit(1)

    session = get_or_create_session(linkedin_profile)
    if not session.campaigns:
        print(f"No campaigns found for {linkedin_profile}.")
        raise SystemExit(1)
    session.campaign = session.campaigns[0]
    return session
