# openoutreach/emails/tasks/send.py
"""EMAIL task — sends one outbound email to an email-reachable qualified lead.

Layer 1 is outbound-only (no inbound reply-reading yet), so email is a single
touch per lead: we can't see replies, and a blind cadence would nag someone who
already answered. The reply lands in the user's inbox; the human takes over.
Automated follow-ups arrive with the inbound reply-reading slice.

Eligibility = a resolved ``Lead.api_email`` + a pre-connect state + no email
sent yet. Volume is paced by a per-mailbox daily cap, counted at send time from
the outgoing email ``ChatMessage`` rows (no aggregate, mirroring LinkedIn's
per-profile connect limit).
"""
from __future__ import annotations

import logging

from django.utils import timezone
from termcolor import colored

from openoutreach.crm.models import DealState

logger = logging.getLogger(__name__)

# Email is orthogonal to the LinkedIn FSM, not a stage in it. An email-having
# lead is routed away from the connect pool (get_qualified_profiles excludes
# api_email leads), so it only ever rests at QUALIFIED — that's where its one
# email fires. NO email state, NO "emailed" state: the outgoing ChatMessage is
# the marker. (Emailing already-connected leads is redundant — the native DM
# takes over there; the harvested contact_info feeds the future central pool.)


def handle_email(task, session, qualifiers):
    from openoutreach.core.agents.follow_up import run_follow_up_agent
    from openoutreach.core.db.summaries import materialize_profile_summary_if_missing
    from openoutreach.emails.sender import send_email

    campaign = session.campaign

    pick = _pick_sendable(campaign)
    if pick is None:
        logger.info("[%s] email: nothing to send (no never-emailed lead with a free box)", campaign)
        return
    deal, mailbox = pick

    public_id = deal.lead.public_identifier
    logger.info("[%s] %s %s via %s", campaign, colored("▶ email", "blue", attrs=["bold"]), public_id, mailbox.from_address)

    materialize_profile_summary_if_missing(deal, session)
    decision = run_follow_up_agent(session, deal, channel="email")

    if decision.action != "send_message":
        # A first-touch opener should always be send_message; anything else
        # (wait / mark_completed with no conversation) just means "skip for now".
        logger.info("[%s] email: agent returned %s for %s — skipping", campaign, decision.action, public_id)
        return

    subject = decision.subject or _fallback_subject(deal)
    message_id = send_email(mailbox, deal.lead.api_email, subject, decision.message)
    _record_sent_email(session, deal, mailbox, subject, decision.message, message_id)
    logger.info("[%s] email sent to %s (%s): %s", campaign, public_id, deal.lead.api_email, subject)


# ── Eligibility + mailbox selection ───────────────────────────────


def _pick_sendable(campaign):
    """First never-emailed eligible deal that has an under-cap box to send from.

    Returns ``(deal, mailbox)`` or ``None``. A deal already bound to a box (only
    possible once follow-ups land) sticks to it; a fresh deal takes the
    least-loaded under-cap box. Capped boxes are skipped, never hopped.
    """
    for deal in emailable_deals(campaign):
        mailbox = _box_for_deal(deal)
        if mailbox is not None:
            return deal, mailbox
    return None


def emailable_deals(campaign):
    """Never-emailed deals reachable by email, oldest first.

    Shared by the handler (pick one to send) and the planner (count the backlog).
    """
    from openoutreach.crm.models import Deal

    return (
        Deal.objects.filter(
            campaign=campaign,
            state__in=EMAILABLE_STATES,
            lead__api_email__isnull=False,
            lead__disqualified=False,
        )
        .exclude(messages__channel="email", messages__is_outgoing=True)
        .select_related("lead", "mailbox")
        .order_by("creation_date")
    )


def pool_remaining_today() -> int:
    """Total sends left across all mailboxes today (Σ per-box headroom).

    0 when email isn't configured (no boxes) or every box is at its cap.
    """
    from openoutreach.emails.models import Mailbox

    return sum(max(0, box.daily_limit - _sent_today(box)) for box in Mailbox.objects.all())


def _box_for_deal(mailbox_owner):
    """The box to send this deal's email from, or None if none is under cap."""
    bound = mailbox_owner.mailbox
    if bound is not None:
        return bound if _under_cap(bound) else None
    return _least_loaded_under_cap_box()


def _least_loaded_under_cap_box():
    """The active box with the most headroom today, or None if all are capped."""
    from openoutreach.emails.models import Mailbox

    under_cap = [(box, sent) for box in Mailbox.objects.all()
                 if (sent := _sent_today(box)) < box.daily_limit]
    if not under_cap:
        return None
    return min(under_cap, key=lambda pair: pair[1])[0]


def _under_cap(box) -> bool:
    return _sent_today(box) < box.daily_limit


def _sent_today(box) -> int:
    """Outgoing emails sent from this box since local midnight (the per-box cap ledger)."""
    from openoutreach.chat.models import ChatMessage

    midnight = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return ChatMessage.objects.filter(
        deal__mailbox=box,
        channel=ChatMessage.Channel.EMAIL,
        is_outgoing=True,
        creation_date__gte=midnight,
    ).count()


# ── Persistence ───────────────────────────────────────────────────


def _record_sent_email(session, deal, mailbox, subject, body, message_id) -> None:
    """Persist the sent email as the deal's outgoing email ChatMessage + bind the box."""
    from openoutreach.chat.models import ChatMessage

    ChatMessage.objects.create(
        deal=deal,
        channel=ChatMessage.Channel.EMAIL,
        external_id=message_id,
        content=body,
        is_outgoing=True,
        owner=session.django_user,
    )
    deal.mailbox = mailbox
    deal.email_subject = subject
    deal.save(update_fields=["mailbox", "email_subject"])


def _fallback_subject(deal) -> str:
    """Last-resort subject if the agent omitted one on the first email."""
    return deal.campaign.name
