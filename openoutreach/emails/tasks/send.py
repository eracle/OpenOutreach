# openoutreach/emails/tasks/send.py
"""EMAIL task — sends the single Layer-1 email for a deal at READY_TO_EMAIL.

Layer 1 is outbound-only and single-shot: the daemon sends one email per
email-reachable lead and never re-emails (follow-ups + replies are the hosted
Layer-2 backend's job, reconstructed straight from the mailbox). So the whole
task is: pick the oldest queued deal + an under-cap box, let the agent compose,
send over SMTP, and record the send on the Deal — which moves it to EMAILED.

Each concern lives where it's cohesive; this module is just the orchestration:
  - the queue (one FSM state)  → ``core.db.deals.get_emailable_deals``
  - the per-box daily cap       → ``emails.models.Mailbox`` pacing manager
  - SMTP transport              → ``emails.sender.send_email``
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone
from termcolor import colored

from openoutreach.chat.models import ChatMessage
from openoutreach.crm.models import DealState

logger = logging.getLogger(__name__)


def handle_email(task, session, qualifiers):
    from openoutreach.core.agents.email_opener import compose_opener_email
    from openoutreach.core.db.deals import get_emailable_deals
    from openoutreach.core.db.summaries import materialize_profile_summary_if_missing
    from openoutreach.emails.models import Mailbox
    from openoutreach.emails.sender import send_email

    campaign = session.campaign

    mailbox = Mailbox.objects.least_loaded_under_cap()
    deal = get_emailable_deals(session).first() if mailbox else None
    if mailbox is None or deal is None:
        logger.info("[%s] email: nothing to send (empty queue or every box at cap)", campaign)
        return

    public_id = deal.lead.public_identifier
    logger.info("[%s] %s %s via %s", campaign,
                colored("▶ email", "blue", attrs=["bold"]), public_id, mailbox.from_address)

    materialize_profile_summary_if_missing(deal, session)
    draft = compose_opener_email(session, deal)

    message_id = send_email(
        mailbox, deal.lead.api_email, draft.subject, draft.body,
        bcc=session.django_user.email,
    )
    _record_sent_email(session, deal, mailbox, draft, message_id)
    logger.info("[%s] email sent to %s (%s): %s\n%s",
                campaign, public_id, deal.lead.api_email, draft.subject, draft.body)


def _record_sent_email(session, deal, mailbox, draft, message_id) -> None:
    """Bind the box, stamp the email fields, record the opener message, move to EMAILED.

    The send record and the state transition live on the same row, so a single
    write commits both: the email can never be sent without leaving READY_TO_EMAIL
    (no double-send window), and EMAILED is never set without its audit fields.
    ``next_follow_up_at`` is seeded from the opener agent's own ``follow_up_hours`` —
    the LLM owns the first gap just as it owns every later one — so the loop reads
    replies + acts when that countdown fires. The opener is also written as the
    thread's first outgoing ChatMessage — the follow-up agent reads it, and the
    per-box cap counts it (``email_message_id`` is the thread root the reply-reader
    matches on).
    """
    now = timezone.now()
    deal.mailbox = mailbox
    deal.email_subject = draft.subject
    deal.email_message_id = message_id
    deal.email_sent_at = now
    deal.next_follow_up_at = now + timedelta(hours=draft.follow_up_hours)
    deal.state = DealState.EMAILED
    deal.save(update_fields=[
        "mailbox", "email_subject", "email_message_id", "email_sent_at",
        "next_follow_up_at", "state",
    ])

    ChatMessage.objects.create(
        deal=deal,
        external_id=message_id,
        content=draft.body,
        is_outgoing=True,
        owner=session.django_user,
        creation_date=now,
    )
