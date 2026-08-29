# openoutreach/enrichment/lookup.py
"""The paid email lookup, in two steps: buy the address, then check on it.

``buy_address`` resolves free sources first — an address already on the lead, then
the hub's cross-operator cache — and only pays the configured finder when both miss.
**Which finder that is, this module does not know**: ``provider.active()`` answers with
whichever vendor the operator holds a key for, and the two differ only in whether they
answer in the same call. An async finder returns a ``request_id`` and nothing waits on
it — the deal parks at FINDING_EMAIL carrying the handle and ``check_lookup`` polls it
later; a sync one is already terminal when ``start`` returns and lands on RESOLVED
without touching FINDING_EMAIL at all.

    already has email  → RESOLVED                (no lookup, no credit)
    free hub-cache hit → RESOLVED                (no provider job, no credit)
    sync finder hit    → RESOLVED                (one call, no handle, no poll)
    async submit       → FINDING_EMAIL           (job submitted, poll from the deal)
    couldn't run       → stays READY_TO_FIND_EMAIL (no key / no credits / API down)

**The free sources are not gated on the paid one.** The key check sits in ``_submit``,
on the paid leg alone, and an empty wallet is a 402 raised from the same place — so an
address already in hand and the hub cache still resolve when there is neither key nor
credit, which is precisely when a free hit is worth the most. The gate used to stand one
level up, in ``cycle._buy_addresses``, where a missing key turned off the free reads too.

**The handle lives on the deal**, not in an external row: ``lookup_request_id`` and
``lookup_attempt`` are columns, so an in-flight job survives a restart and its
backoff (``not_before``) gates that one lead and nothing else. The retired task
queue held both in a queue row whose due-date doubled as the daemon's sleep horizon
— two stalled polls once put a whole install to sleep for 34 hours with 55 deals
ready to send.

**A running job is never abandoned.** There is no deadline and no attempt limit:
the poll interval doubles on the same ``request_id`` and the deal waits. Abandoning
was tried and is worse — it reverted the deal to READY_TO_FIND_EMAIL, where the buy
step bought a *second* job for the same lead, turning a provider outage into 418
submits and 4,512 polls in a week for ~40 leads, none terminating. Doubling makes
waiting nearly free (a week costs 17 polls), and it refuses to mislabel: a timeout
is evidence about the provider, never about whether this person has a findable
address.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone

from openoutreach.core.conf import COLLECT_BACKOFF_BASE_S, COLLECT_BACKOFF_MAX_S
from openoutreach.core.logblock import block_header, step_line
from openoutreach.crm.models import DealState

logger = logging.getLogger(__name__)


def buy_address(deal) -> DealState | None:
    """Resolve this deal's work email, cheapest source first. Returns the next state."""
    from openoutreach.contacts import service as contacts

    logger.info("%s", block_header(
        f"buy_address · {deal.campaign} · {deal.lead.profile_url}", "cyan"))

    # Already in hand — resolved in another campaign, imported, or an earlier hub
    # give-back (Lead is account-level, Deal is campaign-scoped). No lookup, no credit.
    if deal.lead.email:
        logger.info("%s", step_line(
            "known email", "already resolved → RESOLVED", glyph="✓", color="green"))
        return DealState.RESOLVED

    # Free hub cache next — a hit skips the provider job, and the credit, entirely.
    cached = contacts.resolve(deal.lead)
    if cached:
        deal.lead.email = cached
        deal.lead.save(update_fields=["email"])
        logger.info("%s", step_line(
            "hub cache", "hit → RESOLVED", glyph="✓", color="green"))
        return DealState.RESOLVED

    logger.info("%s", step_line("hub cache", "miss"))
    return _submit(deal)


def _submit(deal) -> DealState | None:
    """Run the configured finder — resolving outright, or parking on a job handle.

    Which vendor answers is ``provider.active()``'s decision and nothing here knows the
    name; the branch that matters is *sync or async*, and the provider reports that by
    what ``start`` returns. A synchronous hit lands on RESOLVED without the deal ever
    entering FINDING_EMAIL, so it never occupies a poll slot and never risks the
    stranded-handle case ``reclaim_lookup`` exists for.

    A couldn't-run (no key, no credits, API down) leaves the deal in
    `READY_TO_FIND_EMAIL` — no credit was spent and there is no handle to poll — but
    **backs it off first**. Without that the row is still due on the very next pass,
    so the same deal is re-picked
    immediately and forever: noise every few seconds under the old daemon, and a job that
    never returns now that a bounded run stops only when nothing can advance. Writing
    ``not_before`` is the architecture's one waiting mechanism, and an unreachable
    provider is exactly the case it exists for.
    """
    from openoutreach.enrichment import provider

    finder = provider.active()
    if finder is None:
        logger.info("%s", step_line(
            "finder", "unconfigured — left queued", glyph="⚠", color="yellow"))
        _back_off(deal, advance=True)
        return None

    try:
        lookup = finder.start(deal.lead.profile_url)
    except provider.ProviderUnavailable as exc:
        logger.info("%s", step_line(
            finder.NAME, f"unavailable ({exc}) — left queued",
            glyph="⚠", color="yellow"))
        _back_off(deal, advance=True)
        return None

    if not lookup.pending:
        return _finish(deal, finder, lookup.outcome)

    deal.lookup_request_id = lookup.request_id
    deal.lookup_provider = finder.NAME
    deal.lookup_attempt = 0
    deal.not_before = timezone.now() + timedelta(seconds=COLLECT_BACKOFF_BASE_S)
    logger.info("%s", step_line(
        finder.NAME, f"submitted · req {lookup.request_id[:12]}… → FINDING_EMAIL · polling",
        glyph="✓", color="green"))
    return DealState.FINDING_EMAIL


def reclaim_lookup(deal) -> DealState:
    """Send a FINDING_EMAIL deal that carries no job handle back to be bought.

    ``_submit`` only returns FINDING_EMAIL once it has a ``request_id``, so a deal
    parked here with an empty one has no job to poll and never had a credit spent on
    it. Left alone it is invisible to every query — the poll row skips it and no
    other row claims FINDING_EMAIL — so it waits forever while still counting
    against the day's send headroom. Measured on a live install: two deals stranded
    for 206 hours. Going back to READY_TO_FIND_EMAIL puts it in front of the buy
    step again, under the same spend gate as everyone else.
    """
    logger.info("%s", block_header(
        f"reclaim_lookup · {deal.campaign} · {deal.lead.profile_url}", "yellow"))
    deal.not_before = None
    deal.lookup_attempt = 0
    logger.info("%s", step_line(
        "no job handle", "nothing to poll → READY_TO_FIND_EMAIL",
        glyph="⚠", color="yellow"))
    return DealState.READY_TO_FIND_EMAIL


def check_lookup(deal) -> DealState | None:
    """Poll this deal's in-flight lookup exactly once and act on the outcome.

        hit           → RESOLVED (address stored + given back to the hub)
        miss          → NO_EMAIL_FOUND (terminal — a fit positive the ML keeps)
        still running → back off, stay put
        couldn't poll → retry at the same interval (nothing was learned about the job)
    """
    from openoutreach.enrichment import bettercontact, provider

    logger.info("%s", block_header(
        f"check_lookup · {deal.campaign} · {deal.lead.profile_url}", "magenta",
        meta=f"attempt {deal.lookup_attempt}"))

    # Back to the vendor that minted the handle, never to whichever key is configured
    # now. A blank owner is a row from before the column existed, and every one of
    # those is BetterContact's: it was the only finder, and it is still the only one
    # that issues a handle at all — a synchronous provider never parks a deal here.
    # There is deliberately no configured-key check in front of this: a missing key
    # already raises from inside ``poll_once``, and that path backs the deal off
    # without touching the job, which is exactly the right answer.
    finder = provider.by_name(deal.lookup_provider) or bettercontact

    try:
        outcome = finder.poll_once(deal.lookup_request_id)
    except provider.ProviderUnavailable as exc:
        # Transient — the job is untouched, so wait the same interval and ask again.
        logger.info("%s", step_line(
            "poll", f"unavailable ({exc}) — retrying", glyph="⚠", color="yellow"))
        _back_off(deal, advance=False)
        return None

    if outcome.running:
        _back_off(deal, advance=True)
        logger.info("%s", step_line(
            "running", f"not ready — re-poll in {_human(_delay_for(deal))} "
                       f"(attempt {deal.lookup_attempt})"))
        return None

    return _finish(deal, finder, outcome)


def _finish(deal, finder, outcome) -> DealState:
    """Land a terminated lookup — the one place a hit or a miss becomes a state.

    Shared by both transports on purpose: a synchronous provider reaches it straight
    from ``_submit`` and an async one from ``check_lookup``, and the deal must come to
    rest identically either way. The hub give-back is stamped with the provider that
    paid for the address, so the store records which vendor a contact came from.
    """
    from openoutreach.contacts import service as contacts

    deal.not_before = None
    deal.lookup_request_id = ""

    if not outcome.email:
        logger.info("%s", step_line(
            "no email", "terminal miss → NO_EMAIL_FOUND", glyph="✗", color="yellow"))
        return DealState.NO_EMAIL_FOUND

    deal.lead.email = outcome.email
    _store_identity(deal.lead, outcome)
    contacts.contribute(deal.lead, [outcome.email], finder.NAME)
    logger.info("%s", step_line(
        "hit", f"{outcome.email} → RESOLVED", glyph="✓", color="green"))
    return DealState.RESOLVED


def _store_identity(lead, outcome) -> None:
    """Write the address, and the name parts the provider resolved with it.

    The waterfall derives the contact from the URL and echoes back
    ``contact_first_name``/``contact_last_name`` in the same terminated response — no
    extra call, no extra credit. Taking them from here is why nothing in this codebase
    splits a full name: discovery only ever knows one, and the provider knows the parts.

    The job title is *not* taken. Discovery already stamped one, and that is what the
    qualifier judged the lead on.
    """
    lead.first_name = outcome.first_name
    lead.last_name = outcome.last_name
    lead.save(update_fields=["email", "first_name", "last_name"])


# ── Backoff ───────────────────────────────────────────────────────


def _back_off(deal, *, advance: bool) -> None:
    """Push this deal's next poll out. ``advance`` doubles the interval.

    A genuine still-running poll doubles; a transient provider outage retries at the
    same interval, since nothing was learned about the job. The interval rails at
    ``COLLECT_BACKOFF_MAX_S`` (a month) purely so the schedule stays representable —
    the chain itself never ends.
    """
    if advance:
        deal.lookup_attempt += 1
    deal.not_before = timezone.now() + timedelta(seconds=_delay_for(deal))


def _delay_for(deal) -> float:
    return min(COLLECT_BACKOFF_BASE_S * (2 ** min(deal.lookup_attempt, 64)),
               COLLECT_BACKOFF_MAX_S)


def _human(seconds: float) -> str:
    """A backoff delay at a readable scale — these run from seconds to weeks."""
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds / size:.1f}{unit}"
    return f"{seconds:.0f}s"
