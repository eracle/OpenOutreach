# openoutreach/emails/tasks/follow_up.py
"""EMAIL follow-up task — the agentic loop for one due EMAILED deal.

Ports the LinkedIn follow-up handler onto email: pick the oldest EMAILED deal
whose countdown (``next_follow_up_at``) is due and whose box has headroom, let the
same follow-up agent read the thread (replies via IMAP) and decide, then execute
the decision — a threaded SMTP reply, a completion, or a re-armed wait. Reading
happens at the countdown, exactly as ``sync_conversation`` ran at each LinkedIn
follow-up slot.
"""
from __future__ import annotations

import logging

from django.utils import timezone
from termcolor import colored

from openoutreach.crm.models import DealState

logger = logging.getLogger(__name__)


def _next_follow_up_deal(campaign):
    """Oldest due EMAILED deal in *campaign* whose bound box still has headroom.

    Due = ``next_follow_up_at`` has elapsed. Open = no outcome yet, lead not
    disqualified. A deal whose box is at its daily cap is skipped this cycle (it
    returns when the box frees tomorrow) — the send would fail the cap anyway.
    """
    from openoutreach.crm.models import Deal

    now = timezone.now()
    due = (
        Deal.objects.filter(
            campaign=campaign,
            state=DealState.EMAILED,
            outcome="",
            lead__disqualified=False,
            next_follow_up_at__lte=now,
        )
        .select_related("lead", "mailbox")
        .order_by("next_follow_up_at")
    )
    for deal in due:
        if deal.mailbox and deal.mailbox.headroom_today() > 0:
            return deal
    return None


def handle_follow_up(task, session, qualifiers):
    from openoutreach.core.agents.follow_up import run_follow_up_agent

    campaign = session.campaign

    deal = _next_follow_up_deal(campaign)
    if deal is None:
        logger.info("[%s] follow_up: nothing due (no EMAILED deal past its countdown with box headroom)", campaign)
        return

    public_id = deal.lead.public_identifier
    logger.info("[%s] %s %s", campaign, colored("▶ follow_up", "green", attrs=["bold"]), public_id)

    decision = run_follow_up_agent(session, deal)

    if decision.action == "send_message":
        _send_reply(session, deal, decision)
    elif decision.action == "mark_completed":
        _complete(session, deal, decision)
    elif decision.action == "wait":
        _rearm(deal, decision)


# ── Decision execution ────────────────────────────────────────────


def _send_reply(session, deal, decision) -> None:
    """Send a threaded reply, record it as an outgoing ChatMessage, re-arm the clock."""
    from openoutreach.chat.models import ChatMessage
    from openoutreach.emails.sender import send_email

    subject = _reply_subject(deal.email_subject)
    logger.info("[%s] follow_up reply to %s: %s", deal.campaign, deal.lead.public_identifier, decision.message)
    message_id = send_email(
        deal.mailbox,
        deal.lead.api_email,
        subject,
        decision.message,
        bcc=session.linkedin_profile.linkedin_username,
        in_reply_to=_latest_external_id(deal),
        references=deal.email_message_id,
    )
    now = timezone.now()
    ChatMessage.objects.create(
        deal=deal,
        external_id=message_id,
        content=decision.message,
        is_outgoing=True,
        owner=session.django_user,
        creation_date=now,
    )
    deal.next_follow_up_at = now + _hours(decision.follow_up_hours)
    deal.save(update_fields=["next_follow_up_at"])


def _complete(session, deal, decision) -> None:
    """End the conversation with the agent's chosen outcome."""
    from openoutreach.core.db.deals import set_profile_state

    set_profile_state(session, deal.lead.public_identifier, DealState.COMPLETED.value, outcome=decision.outcome)
    logger.info("[%s] follow_up completed for %s: outcome=%s",
                deal.campaign, deal.lead.public_identifier, decision.outcome)


def _rearm(deal, decision) -> None:
    """No send — just push the countdown out by the agent's chosen interval."""
    deal.next_follow_up_at = timezone.now() + _hours(decision.follow_up_hours)
    deal.save(update_fields=["next_follow_up_at"])


# ── Helpers ───────────────────────────────────────────────────────


def _reply_subject(opener_subject: str) -> str:
    """``Re:`` the opener's subject, without stacking a second ``Re:``."""
    subject = opener_subject or ""
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"


def _latest_external_id(deal) -> str:
    """Message-ID of the newest message in the thread — the reply's ``In-Reply-To``.

    Falls back to the thread root when the thread has no rows yet (shouldn't happen
    on an EMAILED deal, whose opener is always recorded).
    """
    from openoutreach.chat.models import ChatMessage

    latest = (
        ChatMessage.objects.filter(deal=deal)
        .order_by("-creation_date", "-pk")
        .values_list("external_id", flat=True)
        .first()
    )
    return latest or deal.email_message_id


def _hours(follow_up_hours: float):
    from datetime import timedelta

    return timedelta(hours=follow_up_hours)
