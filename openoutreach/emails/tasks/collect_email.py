# openoutreach/emails/tasks/collect_email.py
"""COLLECT_EMAIL task — the *poll* leg of the paid email lookup.

The bound counterpart to ``find_email``: it polls one in-flight provider job
(the ``request_id`` the submit leg parked in this task's payload) exactly once,
then acts on the outcome:

    hit          → READY_TO_EMAIL   (address set + given back to the hub; 1 credit)
    miss         → NO_EMAIL_BETTERCONTACT (terminal — a fit positive the ML keeps)
    still running → chain the next poll with a doubled backoff, unless past the
                    give-up deadline → revert FINDING_EMAIL → READY_TO_FIND_EMAIL
    couldn't poll → retry with the same backoff (transient outage, deadline-bounded)

Each still-running poll mints its successor (``attempt + 1``), so exactly one
live collect task exists per in-flight lookup — the chain, not the drain guard,
maintains that invariant. The request_id, backoff attempt, and deadline all live
in the payload, so the lookup survives a daemon restart on the persisted row.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from django.utils import timezone

from openoutreach.core.conf import (
    COLLECT_BACKOFF_BASE_S,
    COLLECT_BACKOFF_MAX_S,
    COLLECT_DEADLINE_S,
)
from openoutreach.core.logblock import block_header, step_line
from openoutreach.crm.models import DealState

logger = logging.getLogger(__name__)


def handle_collect_email(task, session, qualifiers):
    from openoutreach.crm.models import Deal
    from openoutreach.emails.bettercontact import BetterContactUnavailable

    campaign = session.campaign
    p = task.payload
    deal = (
        Deal.objects.filter(pk=p.get("deal_id"), state=DealState.FINDING_EMAIL)
        .select_related("lead")
        .first()
    )
    if deal is None:
        # The deal moved on (or was reset) since this poll was scheduled — the
        # chain is stale, so let it end here rather than act on a wrong state.
        logger.info("[%s] collect_email: deal %s no longer FINDING_EMAIL — dropping poll", campaign, p.get("deal_id"))
        return

    public_id = deal.lead.profile_url
    logger.info("%s", block_header(
        f"collect_email · {campaign} · {public_id}", "magenta", meta=f"attempt {p.get('attempt', 0)}"))

    try:
        outcome = _poll(p["provider"], p["request_id"])
    except BetterContactUnavailable as exc:
        # Transient — retry with the same backoff, still bounded by the deadline.
        logger.info("%s", step_line("poll", f"unavailable ({exc}) — retrying", glyph="⚠", color="yellow"))
        _reschedule_or_give_up(session, public_id, p, advance=False)
        return

    if outcome.hit:
        _on_hit(session, campaign, deal, public_id, outcome.email)
    elif outcome.miss:
        _on_miss(session, public_id)
    else:  # still running
        _reschedule_or_give_up(session, public_id, p, advance=True)


def _poll(provider: str, request_id: str):
    from openoutreach.emails import bettercontact

    if provider == "bettercontact":
        return bettercontact.poll_once(request_id)
    raise ValueError(f"unknown email provider: {provider}")


# ── Outcome handling ──────────────────────────────────────────────────


def _on_hit(session, campaign, deal, public_id, email) -> None:
    """Persist the address, give it back to the hub (paid hit), route to send."""
    from openoutreach.contacts import service as contacts
    from openoutreach.core.db.deals import set_profile_state
    from openoutreach.core.scheduler import flush_email_queue

    deal.lead.email = email
    deal.lead.save(update_fields=["email"])
    contacts.contribute(session, deal.lead, [email], contacts.ORIGIN_BETTERCONTACT)
    set_profile_state(session, public_id, DealState.READY_TO_EMAIL.value, log=False)
    # Queue the opener now so the send preempts the next find_email on claim.
    flush_email_queue(session, campaign)
    logger.info("%s", step_line("hit", f"{email} → {DealState.READY_TO_EMAIL.name}", glyph="✓", color="green"))


def _on_miss(session, public_id) -> None:
    """Terminal miss — enrichment found no address. Parks at its own terminal state
    (NO_EMAIL_BETTERCONTACT), distinct from FAILED: the lead was a fit positive
    (the ML labeler keeps it as label=1), only reachability failed. The dedicated
    state also gives downstream work a hook to build on (e.g. retry via another
    provider)."""
    from openoutreach.core.db.deals import set_profile_state

    set_profile_state(session, public_id, DealState.NO_EMAIL_BETTERCONTACT.value, log=False)
    logger.info("%s", step_line(
        "no email", f"terminal miss → {DealState.NO_EMAIL_BETTERCONTACT.name}", glyph="✗", color="yellow"))


def _reschedule_or_give_up(session, public_id, payload, advance: bool) -> None:
    """Chain the next poll, or revert to READY_TO_FIND_EMAIL past the deadline.

    ``advance`` doubles the backoff (a genuine still-running poll); a transient
    outage retries at the same backoff. Either way the give-up deadline
    (``submitted_at + COLLECT_DEADLINE_S``) is the hard bound: past it, the job is
    abandoned and the deal re-queues for a fresh submit (no credit was spent).
    """
    from openoutreach.core.db.deals import set_profile_state
    from openoutreach.core.scheduler import schedule_collect_email

    submitted_at = datetime.fromisoformat(payload["submitted_at"])
    if timezone.now() >= submitted_at + timedelta(seconds=COLLECT_DEADLINE_S):
        set_profile_state(session, public_id, DealState.READY_TO_FIND_EMAIL.value, log=False)
        logger.info("%s", step_line(
            "deadline", f"poll deadline exceeded → {DealState.READY_TO_FIND_EMAIL.name} · re-queued for a fresh submit",
            glyph="⚠", color="yellow"))
        return

    attempt = payload.get("attempt", 0) + 1 if advance else payload.get("attempt", 0)
    delay = min(COLLECT_BACKOFF_BASE_S * (2 ** attempt), COLLECT_BACKOFF_MAX_S)
    schedule_collect_email(payload={**payload, "attempt": attempt}, delay_seconds=delay)
    # A genuine still-running poll reports its next wake-up; a transient outage
    # already logged its ⚠ retry step above, so don't double up.
    if advance:
        logger.info("%s", step_line("running", f"not ready — re-poll in {delay}s (attempt {attempt})"))
