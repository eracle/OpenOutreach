# openoutreach/core/session.py
"""Browserless run session.

The email-only replacement for the old LinkedIn ``AccountSession``: it carries
the operator's identity and campaign context for the daemon and the agents, but
owns no browser — there is nothing to log into, scrape, or reauthenticate. The
operator is just the Django ``User`` running the daemon; ``self_profile`` is
synthesized from that user and the ``SiteConfig`` country rather than scraped.
"""
from __future__ import annotations

import logging
from functools import cached_property

logger = logging.getLogger(__name__)

_sessions: dict[int, "OperatorSession"] = {}


class OperatorSession:
    def __init__(self, user):
        self.django_user = user

        # Active campaign — set by the daemon before each task execution.
        self.campaign = None

    @cached_property
    def campaigns(self):
        """All campaigns this user belongs to (cached)."""
        from openoutreach.core.models import Campaign
        return list(Campaign.objects.filter(users=self.django_user))

    @cached_property
    def self_profile(self) -> dict:
        """The operator's own identity, synthesized (not scraped).

        Name comes from the Django user (the agents read ``first_name`` for the
        seller binding, falling back to the username), country from ``SiteConfig``.
        The contacts store uses ``public_identifier`` (the operator email) as the
        stable operator key.
        """
        from openoutreach.core.models import SiteConfig

        user = self.django_user
        return {
            "public_identifier": user.email or user.username,
            "first_name": user.first_name or "",
            "last_name": user.last_name or "",
            "country_code": SiteConfig.load().country_code or "",
        }

    @cached_property
    def active_timezone(self) -> str | None:
        """IANA zone for the active-hours window — the ``ACTIVE_TIMEZONE`` conf
        override, else inferred from the operator's country (from onboarding),
        else None (no gating)."""
        from openoutreach.core.conf import ACTIVE_TIMEZONE
        from openoutreach.core.models import SiteConfig
        from openoutreach.core.tz_country import timezone_for_country

        if ACTIVE_TIMEZONE:
            return ACTIVE_TIMEZONE
        return timezone_for_country(SiteConfig.load().country_code)

    def active_timezone_provenance(self) -> str:
        """Human-readable note on where ``active_timezone`` came from — shown in
        the daemon's active-hours log."""
        from openoutreach.core.conf import ACTIVE_TIMEZONE
        from openoutreach.core.models import SiteConfig

        if ACTIVE_TIMEZONE:
            return f"{ACTIVE_TIMEZONE} (configured via ACTIVE_TIMEZONE)"
        country = (SiteConfig.load().country_code or "?").upper()
        tz = self.active_timezone
        if tz:
            return f"{tz} (inferred from operator country {country}; override with ACTIVE_TIMEZONE)"
        return "unset (no country and no ACTIVE_TIMEZONE) — not gating"

    def __repr__(self) -> str:
        return self.django_user.email or self.django_user.username


def get_active_user():
    """Return the Django ``User`` running the daemon (the onboarded operator)."""
    from django.contrib.auth.models import User

    return User.objects.filter(is_active=True, is_staff=True).order_by("pk").first()


def reconcile_operator_email() -> bool:
    """Bind the operator's ``email`` to the mailbox ``from_address`` — the single
    source of truth for who the daemon sends (and BCCs itself) as.

    Self-heals installs whose operator ``User`` predates email-first onboarding
    (blank email → BCC self-copy and the contacts give-back silently no-op), and
    any later drift if the operator swaps mailbox. Returns True if it changed the
    email. No-op (returns False) when there is no operator or no mailbox yet — a
    fresh install still mid-onboarding derives the email at account creation.
    """
    from openoutreach.emails.models import Mailbox

    user = get_active_user()
    box = Mailbox.objects.first()
    if user is None or box is None or user.email == box.from_address:
        return False

    previous = user.email or "(blank)"
    user.email = box.from_address
    user.save(update_fields=["email"])
    logger.info("Reconciled operator email: %s -> %s", previous, box.from_address)
    return True


def get_or_create_session(user) -> "OperatorSession":
    pk = user.pk
    if pk not in _sessions:
        _sessions[pk] = OperatorSession(user)
        logger.debug("Created operator session for %s", user)
    return _sessions[pk]
