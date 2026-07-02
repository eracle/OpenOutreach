# openoutreach/emails/mailbox_setup.py
"""Mailbox import: parse an App-Passwords paste, auth-check, and store each box.

The console-free half of connecting a sending mailbox (the interactive prompts
live in onboarding). A row exists iff its credentials authenticated — the
provider has no health API, so the import *is* the gate; per-box auth failures
are collected in the report, not raised.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from openoutreach.core.conf import DEFAULT_EMAIL_DAILY_LIMIT
from openoutreach.emails.icemail import parse_mailboxes
from openoutreach.emails.models import Mailbox
from openoutreach.emails.smtp import verify_auth


@dataclass
class ImportReport:
    parsed: int = 0
    stored: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)  # (email, reason)


def import_mailboxes(pasted: str, daily_limit: int = DEFAULT_EMAIL_DAILY_LIMIT) -> ImportReport:
    """Parse an App-Passwords paste, then auth-check and store each box.

    Raises ValueError (from ``parse_mailboxes``) when the paste isn't the App
    Passwords sheet; per-box auth failures are collected in the report, not raised.
    """
    return _store_mailboxes(parse_mailboxes(pasted), daily_limit)


def _store_mailboxes(rows: list[tuple[str, str]], daily_limit: int) -> ImportReport:
    """Auth-check each ``(email, app_password)`` and store only the ones that log in.

    ``daily_limit`` is the warm-safe sends/day applied to each stored box.
    """
    report = ImportReport()
    for email, password in rows:
        report.parsed += 1
        box = Mailbox(username=email, password=password, from_address=email)
        ok, reason = verify_auth(box.host, box.port, box.username, box.password)
        if not ok:
            report.failures.append((email, reason))
            continue
        Mailbox.objects.update_or_create(
            username=email,
            defaults={"password": password, "from_address": email, "daily_limit": daily_limit},
        )
        report.stored += 1
    return report
