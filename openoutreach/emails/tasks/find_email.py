# openoutreach/emails/tasks/find_email.py
"""FIND_EMAIL task — the *submit* leg of the paid email lookup.

Drives the discovery→qualify→rank chain to surface one top-ranked
READY_TO_FIND_EMAIL deal, then starts resolving its work email:

    free hub-cache hit → READY_TO_EMAIL directly (no provider job, no credit)
    hub miss           → submit a provider job, park the deal at FINDING_EMAIL,
                         and hand off to the collect leg (``collect_email``),
                         which polls the job's ``request_id`` to termination
    couldn't submit    → stay READY_TO_FIND_EMAIL (no key / API down — retry next cycle)

This leg never blocks on the result: submitting returns a ``request_id``, and the
bound ``collect_email`` task owns the poll + backoff. The job handle lives in that
task's payload, never on the deal, so an in-flight lookup rides entirely on the
persisted task row and survives a daemon restart.
"""
from __future__ import annotations

import logging

from django.utils import timezone
from termcolor import colored

from openoutreach.core.conf import COLLECT_BACKOFF_BASE_S
from openoutreach.crm.models import DealState

logger = logging.getLogger(__name__)

_PROVIDER = "bettercontact"


def _select_candidate(session, campaign, qualifier):
    """Pick the next lead to look up an email for, ensuring it has a Deal.

    Freemium campaigns draw from the kit-ranked freemium pool and mint the Deal on
    the fly (the kit model ranks in place of the GP gate); regular campaigns draw
    from the rank-gated READY_TO_FIND_EMAIL pool, where the Deal already exists.
    """
    if campaign.is_freemium:
        from openoutreach.core.db.deals import create_freemium_deal
        from openoutreach.core.pipeline.freemium_pool import find_freemium_candidate

        candidate = find_freemium_candidate(session, qualifier)
        if candidate is not None:
            create_freemium_deal(session, candidate["profile_url"])
        return candidate

    from openoutreach.core.pipeline.pools import find_candidate

    return find_candidate(session, qualifier)


def handle_find_email(task, session, qualifiers):
    from openoutreach.core.db.deals import set_profile_state
    from openoutreach.crm.models import Deal
    from openoutreach.emails.models import has_mailbox

    campaign = session.campaign

    # No mailbox → nothing to send even on a hit, so resolving an address (and
    # spending a credit) is pointless. Onboarding collects the mailbox; until
    # one is connected the leg is idle.
    if not has_mailbox():
        logger.info("[%s] find_email: no mailbox — leg idle until one is connected", campaign)
        return

    qualifier = qualifiers.get(campaign.pk)
    candidate = _select_candidate(session, campaign, qualifier)
    if candidate is None:
        logger.info("[%s] find_email: no ranked candidate awaiting a lookup", campaign)
        return

    public_id = candidate["profile_url"]
    deal = (
        Deal.objects.filter(lead__profile_url=public_id, campaign=campaign)
        .select_related("lead")
        .first()
    )
    if deal is None:
        logger.warning("[%s] find_email: no Deal for %s", campaign, public_id)
        return

    logger.info("[%s] %s %s", campaign, colored("▶ find_email", "cyan", attrs=["bold"]), public_id)

    # Free hub cache first — a hit skips the provider job (and the credit) entirely.
    if _try_hub_cache(session, deal.lead):
        set_profile_state(session, public_id, DealState.READY_TO_EMAIL.value)
        _mint_email_slot(session, campaign)
        return

    _submit_lookup(session, campaign, deal, public_id)


def _try_hub_cache(session, lead) -> bool:
    """Resolve + persist a work email from the free cross-operator hub cache.

    Returns True on a hit (``email`` set, cached). A cached hit is not
    re-contributed to the hub.
    """
    from openoutreach.contacts import service as contacts

    cached_email = contacts.resolve(lead)
    if not cached_email:
        return False
    lead.email = cached_email
    lead.save(update_fields=["email"])
    return True


def _submit_lookup(session, campaign, deal, public_id) -> None:
    """Submit the paid provider job and hand off to the collect leg.

    On a successful submit the deal parks at FINDING_EMAIL (excluded from the
    candidate pool so the next slot can't re-submit it) and a first
    ``collect_email`` poll is scheduled. A couldn't-submit (no key / API down)
    leaves the deal at READY_TO_FIND_EMAIL to retry — no credit is spent and no
    poll is scheduled.
    """
    from openoutreach.core.db.deals import set_profile_state
    from openoutreach.core.scheduler import schedule_collect_email
    from openoutreach.emails import bettercontact
    from openoutreach.emails.bettercontact import BetterContactQuery, BetterContactUnavailable

    if not bettercontact.is_configured():
        logger.info("[%s] find_email: finder unconfigured for %s — leaving queued", campaign, public_id)
        return

    try:
        request_id = bettercontact.submit(BetterContactQuery(linkedin_url=deal.lead.profile_url))
    except BetterContactUnavailable as exc:
        logger.info("[%s] find_email: submit unavailable for %s (%s) — leaving queued", campaign, public_id, exc)
        return

    now = timezone.now()
    set_profile_state(session, public_id, DealState.FINDING_EMAIL.value)
    schedule_collect_email(
        payload={
            "campaign_id": campaign.pk,
            "deal_id": deal.pk,
            "provider": _PROVIDER,
            "request_id": request_id,
            "submitted_at": now.isoformat(),
            "attempt": 0,
        },
        delay_seconds=COLLECT_BACKOFF_BASE_S,
    )
    logger.info("[%s] find_email: submitted %s (req %s) — polling", campaign, public_id, request_id)


def _mint_email_slot(session, campaign) -> None:
    """Queue an opener for a freshly-ready deal so the send preempts the next
    find_email on the very next claim (email outranks find_email in the queue)."""
    from openoutreach.core.scheduler import flush_email_queue

    flush_email_queue(session, campaign)
