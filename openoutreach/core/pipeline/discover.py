# openoutreach/core/pipeline/discover.py
"""Discovery leg — fetch the GP's chosen maximal into first-touch Leads.

The top of the funnel: a page of ICP-matched rows becomes ``Lead`` rows (embedded
with their retrieving query's keywords + profile_text) awaiting qualification. Free
(Lead Finder bills nothing) and browserless. The qualify chain calls ``discover``
whenever its candidate pool runs dry; each call fetches the single maximal the GP
scores highest — a fresh region to explore or a proven vein to page deeper.

``discover`` takes the qualifier because the GP is now the query selector too: it
scores every candidate maximal by its keywords (``select.next_query``). Two things
grow the vocabulary, both here, neither a GP-confidence gate:

- **throughput** — every ``MINT_EVERY_N_QUALIFIED`` new qualified leads, mint clauses
  from them (fold in what they taught us) before selecting;
- **saturation** — the selector returns ``None`` (nothing fetchable), so mint, and
  stop only if minting adds nothing.

See ``select.py`` and the roadmap card ``p2-e3-discovery-unified-gp-query-selection``.
"""
from __future__ import annotations

import logging

from termcolor import colored

logger = logging.getLogger(__name__)


def _label(offset: int) -> str:
    """Colour the move by what it is — green deepens a vein, yellow explores fresh."""
    name, colour = ("deepen", "green") if offset else ("explore", "yellow")
    return colored(name, colour, attrs=["bold"])


def _qualified_count(campaign) -> int:
    """Leads the LLM has accepted — any deal that is not a ``wrong_fit`` rejection."""
    from openoutreach.crm.models import Deal, DealState, Outcome

    return (
        Deal.objects.filter(campaign=campaign, lead_id__isnull=False)
        .exclude(state=DealState.FAILED, outcome=Outcome.WRONG_FIT).count()
    )


def discover(session, qualifier) -> int:
    """Fetch one maximal's page and persist its first-touch Leads. Returns the count.

    Seeds the pool on a cold start, folds qualified learnings in on the throughput
    cadence, then fetches the GP's top-scored maximal. An empty page is recorded (its
    two shapes differently) and the next candidate tried — the loop keeps firing the
    next-best query until one returns leads, so a run of dead maximals never yields an
    empty-handed pass while any live query remains. Returns 0 only when the pool is
    exhausted (minting adds nothing) or when a fetch is unavailable (best-effort — a
    provider outage must not fail the enclosing find_email task).

    Cost of never capping: on a mostly-dead ICP one call can fire the whole remaining
    pool serially before it saturates, and each fetch is a blocking ~45s provider
    call. Termination is still guaranteed — the pool is finite and every empty query
    is recorded + exhausted, so the candidate set strictly shrinks each iteration.

    Gated as before: freemium campaigns seed from their kit, and a campaign with no
    finder key or no product/target can't be searched.
    """
    from openoutreach.core.conf import CAMPAIGN_CONFIG
    from openoutreach.core.db.leads import create_lead
    from openoutreach.core.pipeline import select
    from openoutreach.core.pipeline.icp import generate_seed
    from openoutreach.core.pipeline.mint import mint_clauses
    from openoutreach.discovery import clause_terms, describe_clauses, filters_for, search
    from openoutreach.emails import bettercontact

    campaign = session.campaign
    if campaign.is_freemium:
        return 0
    if not bettercontact.is_configured():
        return 0
    if not (campaign.product_docs or campaign.campaign_target):
        return 0

    logger.info(colored("▶ discover", "blue", attrs=["bold"]))

    if not campaign.clauses.exists():
        generate_seed(campaign)

    # Throughput mint: fold in the leads that qualified since the last mint.
    qualified = _qualified_count(campaign)
    if qualified and qualified - campaign.discovery_minted_at_qualified >= CAMPAIGN_CONFIG["mint_every_n_qualified"]:
        mint_clauses(campaign)

    empties = 0
    minted = False
    while True:
        query = select.next_query(campaign, qualifier)
        if query is None:
            # Saturation: the pool spans nothing fetchable. Widen the axes once — if the
            # new clauses open a fetchable maximal, reselect; otherwise stop (the pool
            # is bigger now, so the next call retries). One mint per call bounds the
            # loop when every new maximal is still empty-pruned.
            if not minted and mint_clauses(campaign) > 0:
                minted = True
                continue
            logger.info("[%s] discovery exhausted — pool fully spanned (%d dead quer%s this pass)",
                        campaign, empties, "y" if empties == 1 else "ies")
            return 0

        filters = filters_for(query.clauses)
        try:
            rows = search(filters, limit=select.DISCOVERY_PAGE_SIZE, offset=query.offset)
        except bettercontact.BetterContactUnavailable as exc:
            # Best-effort: a provider outage or an un-fetchable query must not fail the
            # caller. Retire the query (persist so it isn't re-picked, exhaust so it
            # isn't deepened) but do NOT record it empty — we don't know it matches
            # nobody, only that we couldn't fetch it.
            logger.warning("[%s] %s: fetch of %s unavailable (%s) — retiring it",
                           campaign, _label(query.offset),
                           colored(describe_clauses(query.clauses), "cyan"), exc)
            select.persist_fetched(campaign, query.clauses, query.offset)
            select.mark_exhausted(campaign, query.clauses)
            return 0

        if rows:
            node = select.persist_fetched(campaign, query.clauses, query.offset)
            query_terms = clause_terms(query.clauses)
            created = sum(
                create_lead(row, country_code=campaign.country_code,
                            discovered_by=node, query_terms=query_terms)
                for row in rows
            )
            logger.info("[%s] %s: %s new lead(s) from %d row(s) (offset %d) — %s",
                        campaign, _label(query.offset),
                        colored(str(created), "green", attrs=["bold"]),
                        len(rows), query.offset,
                        colored(describe_clauses(query.clauses), "cyan"))
            return created

        # Empty page — record what it means, then try the next candidate. offset 0
        # convicts the maximal (matches nobody); a deeper empty is a vein run dry.
        if query.offset == 0:
            select.record_empty(query.clauses)
            logger.info("[%s] %s: %s — no one matches this whole combination (%s); recording it "
                        "so it, and any narrower query that also contains these clauses, is "
                        "skipped from now on",
                        campaign, _label(query.offset),
                        colored("dead end", "yellow", attrs=["bold"]),
                        colored(describe_clauses(query.clauses), "cyan"))
        else:
            logger.info("[%s] %s: %s — no more leads past offset %d for %s; marking it used up",
                        campaign, _label(query.offset),
                        colored("vein empty", "yellow", attrs=["bold"]), query.offset,
                        colored(describe_clauses(query.clauses), "cyan"))
        select.persist_fetched(campaign, query.clauses, query.offset)
        select.mark_exhausted(campaign, query.clauses)
        empties += 1
        # No cap: loop back and try the next-best query. Each empty is now recorded +
        # exhausted, so next_query won't re-pick it and the candidate set shrinks —
        # the loop ends at saturation (next_query is None), not on a dead-query count.
