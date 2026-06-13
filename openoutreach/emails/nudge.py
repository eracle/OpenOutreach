# openoutreach/emails/nudge.py
"""Per-launch email-setup nudge.

Runs every `rundaemon` start after onboarding. Until both a finder key and a
working mailbox exist, it prompts (on a TTY) or logs (headless) the next setup
step — copy drawn from the GLF angle in marketing/email-sequence.md, filled with
the user's own pipeline numbers. Never blocks: email is a deferrable upgrade.
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field

import questionary

from openoutreach.core.conf import DEFAULT_CONNECT_DAILY_LIMIT, DEFAULT_EMAIL_DAILY_LIMIT
from openoutreach.core.models import SiteConfig
from openoutreach.core.onboarding_wizard import _BACK, MultilineText
from openoutreach.crm.models import Deal, DealState, Lead
from openoutreach.emails.icemail import parse_mailboxes
from openoutreach.emails.models import Mailbox
from openoutreach.emails.smtp import verify_auth
from openoutreach.linkedin.models import LinkedInProfile

logger = logging.getLogger(__name__)

FINDER_AFFILIATE_URL = "https://bettercontact.rocks?fpr=openoutreach"
SENDER_AFFILIATE_URL = "https://icemail.ai?via=openoutreach"

NO_FINDER = "no_finder"
NO_MAILBOX = "no_mailbox"
CONFIGURED = "configured"


# ── Setup state ──────────────────────────────────────────────────

def email_state() -> str:
    """Which setup step is next: NO_FINDER, NO_MAILBOX, or CONFIGURED."""
    if not SiteConfig.load().finder_api_key:
        return NO_FINDER
    if not Mailbox.objects.exists():
        return NO_MAILBOX
    return CONFIGURED


# ── Nudge copy ───────────────────────────────────────────────────

# GAIN/LOGIC — the discovery engine works; the volume channel is missing.
NO_FINDER_NUDGE = """
📧  Reach your qualified leads by email — LinkedIn finds them, email closes them.
    Your model has qualified {qualified} leads; LinkedIn safely sends only
    ~{connect_cap} connects/day. Email reaches the same list far faster.
    Turn on enrichment (a paid finder — that's how the tool stays free):
      {finder_url}
"""

# FEAR — the qualified leads found, then left unreached behind the connect cap.
NO_MAILBOX_NUDGE = """
📧  {pending} qualified leads sent connection requests that were never accepted —
    aging out behind LinkedIn's daily cap.
    {resolved_emails} of your leads already have an email resolved and waiting.
    Finish email setup to reach them.
    Cold-email mailboxes (IceMail — paid; needs ~2-week warmup):
      {sender_url}
"""


def render(state: str, stats: dict) -> str:
    """The nudge copy for *state*, filled with the user's pipeline numbers."""
    template = NO_FINDER_NUDGE if state == NO_FINDER else NO_MAILBOX_NUDGE
    return template.format(
        finder_url=FINDER_AFFILIATE_URL, sender_url=SENDER_AFFILIATE_URL, **stats
    )


def pipeline_stats() -> dict:
    """The user's own numbers — what makes the nudge land instead of nag."""
    profile = LinkedInProfile.objects.filter(active=True).first()
    return {
        "qualified": Deal.objects.filter(state=DealState.QUALIFIED).count(),
        "pending": Deal.objects.filter(state=DealState.PENDING).count(),
        "resolved_emails": Lead.objects.filter(api_email__isnull=False).count(),
        "connect_cap": profile.connect_daily_limit if profile else DEFAULT_CONNECT_DAILY_LIMIT,
    }


# ── Mailbox import (parse → store → auth-check; no console I/O) ───

@dataclass
class ImportReport:
    parsed: int = 0
    stored: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)  # (email, reason)


def import_mailboxes(pasted: str, daily_limit: int = DEFAULT_EMAIL_DAILY_LIMIT) -> ImportReport:
    """Auth-check every mailbox in a pasted export block, storing only the ones
    whose credentials log in. A row exists iff it authenticated — there is no
    inactive state to carry. ``daily_limit`` is the warm-safe sends/day applied
    to each imported box (set once at onboarding, like the LinkedIn limit).

    Raises ValueError if the paste has no recognizable header row.
    """
    report = ImportReport()
    for email, password in parse_mailboxes(pasted):
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


# ── Interactive prompt ───────────────────────────────────────────

def prompt_email_setup() -> None:
    """Show the next setup step; collect config interactively on a TTY.

    Skipping (empty input / Ctrl+D), a bad paste, and a failing auth-check are
    all handled gracefully and re-ask next launch — there is no opt-out, by
    design. Headless runs log the nudge instead of prompting. Unlike onboarding
    this never `sys.exit`s, so it can't block the LinkedIn discovery leg.
    """
    state = email_state()
    if state == CONFIGURED:
        return

    message = render(state, pipeline_stats())
    if not sys.stdin.isatty():
        logger.info(message)
        return

    print(message)
    _COLLECT_BY_STATE[state]()


def _collect_finder_key() -> None:
    key = questionary.password("Finder API key (Enter to skip):").ask()
    if not key or not key.strip():
        return
    cfg = SiteConfig.load()
    cfg.finder_api_key = key.strip()
    cfg.save()
    logger.info("Finder key saved — enrichment is on; emails resolve as leads qualify.")


def _collect_mailboxes() -> None:
    pasted = _ask_for_paste()
    if pasted is None:
        return
    daily_limit = _ask_for_daily_limit()
    try:
        report = import_mailboxes(pasted, daily_limit=daily_limit)
    except ValueError as exc:
        print(f"  {exc}")
        return
    _print_report(report)


def _ask_for_daily_limit() -> int:
    """Per-mailbox warm-safe sends/day; Enter accepts the conservative default."""
    answer = questionary.text(
        "Emails per mailbox per day (Enter for default):",
        default=str(DEFAULT_EMAIL_DAILY_LIMIT),
    ).ask()
    try:
        value = int((answer or "").strip())
        return value if value > 0 else DEFAULT_EMAIL_DAILY_LIMIT
    except (TypeError, ValueError):
        return DEFAULT_EMAIL_DAILY_LIMIT


def _ask_for_paste() -> str | None:
    """Prompt for the pasted export; None if the user skips."""
    answer = MultilineText(
        "mailboxes",
        "Paste the IceMail Export Mailboxes sheet (with its header row)",
        required=False,
    ).ask("")
    return None if not answer or answer == _BACK else answer


def _print_report(report: ImportReport) -> None:
    for email, reason in report.failures:
        print(f"  ✗ {email}: {reason}")
    if not report.parsed:
        print("  No mailboxes found — include the header row (Email, Password, …).")
        return
    print(f"  Parsed {report.parsed} mailbox(es); {report.stored} authenticated and saved.")


_COLLECT_BY_STATE = {NO_FINDER: _collect_finder_key, NO_MAILBOX: _collect_mailboxes}
