# openoutreach/core/pipeline/discover.py
"""Discovery leg — fetch the GP's chosen maximal into first-touch Leads.

The top of the funnel: a page of ICP-matched rows becomes ``Lead`` rows (embedded
with their retrieving query's keywords + profile_text) awaiting qualification. Free
(Lead Finder bills nothing) and browserless. The qualify chain calls ``discover``
whenever its candidate pool runs dry; each call fetches the single maximal the GP
scores highest — a fresh region to explore or a proven vein to page deeper.

``discover`` takes the qualifier because the GP is now the query selector too: it
scores every candidate by its keywords (``select.next_query``). Two things grow the
vocabulary, both here, neither a GP-confidence gate:

- **throughput** — every ``MINT_EVERY_N_QUALIFIED`` new qualified leads, mint clauses
  from them (fold in what they taught us) before selecting;
- **saturation** — the selector returns ``None`` (nothing fetchable), so mint, and
  stop only if minting adds a value that survives the pre-screen.

Every batch of new clauses — the cold-start seed and every mint — is **pre-screened**
(``_prescreen``) before it composes any maximal: each new value is fetched **truly
alone** (no headcount band, no sibling clauses — the check is only whether the keyword
means anything to Lead Finder), its full page harvested like any other query, and a
value that matches nobody is dropped from the pool so no product slab is ever built on
a dead axis. Probing the value alone is what makes the size-1 empty record *sound* to
write globally — it convicts the value itself, not the value-within-this-band. When a
fired query matches nobody, ``select`` backs off to its one-clause-looser
generalizations — the ``discover`` loop just keeps recording empties and re-selecting;
the backoff itself lives in ``select._candidates``. See ``select.py``, ``mint.py`` and
the roadmap cards ``p2-e3-discovery-unified-gp-query-selection`` and
``p2-e3-discovery-empty-set-backoff``.
"""
from __future__ import annotations

import logging

from termcolor import colored

logger = logging.getLogger(__name__)


def _move(offset: int) -> str:
    """Name the move: ``deepen`` pages a known vein, ``explore`` opens a fresh one."""
    return "deepen" if offset else "explore"


def _qualified_count(campaign) -> int:
    """Leads the LLM has accepted — any deal that is not a ``wrong_fit`` rejection."""
    from openoutreach.crm.models import Deal, DealState, Outcome

    return (
        Deal.objects.filter(campaign=campaign, lead_id__isnull=False)
        .exclude(state=DealState.FAILED, outcome=Outcome.WRONG_FIT).count()
    )



def _harvest(campaign, clauses, offset: int, rows: list[dict]) -> int:
    """Persist a fetched page as first-touch Leads, each keyworded by the retrieving
    query so its terms ride the embedding (``db/leads.create_lead``).

    Shared by the main walk and the pre-screen phase: every query that returns rows
    creates leads, at any depth — there is no diagnostic-only fetch. Returns the count
    of leads newly created (a re-surfaced profile keeps its original ``discovered_by``).
    """
    from openoutreach.core.db.leads import create_lead
    from openoutreach.core.pipeline import select
    from openoutreach.discovery import clause_terms

    node = select.persist_fetched(campaign, clauses, offset)
    query_terms = clause_terms(clauses)
    return sum(
        create_lead(row, country_code=campaign.country_code,
                    discovered_by=node, query_terms=query_terms)
        for row in rows
    )


def _prescreen(campaign, new_pairs) -> int:
    """Probe each new clause value **truly alone** and drop any the provider matches
    nobody with, so no maximal is ever composed from a dead axis value.

    Runs at clause generation — the cold-start seed and every mint — so each value is
    probed exactly once, before it enters the Cartesian product. The probe is an
    ordinary fetch of the value **by itself** — no headcount band, no sibling clauses:
    the only question is whether the keyword means anything to Lead Finder at all. A
    value with support harvests its full page like any other query (the CRM holds every
    profile; which of them to act on is decided downstream, not here); a value that
    matches nobody is removed from the pool and recorded empty as the size-1 set of the
    value alone. Probing the value alone is exactly what makes that singleton record
    **sound to write globally**: it convicts the value itself — nothing else to blame —
    so the cross-campaign prune it drives (``EmptyClauseSet`` carries no campaign FK) is
    a true fact about the provider's index, not "empty within this campaign's size
    band". A singleton empty prunes every maximal that contains the value *and*
    generates no backoff generalization, so a pre-screened-dead value can never be
    resurrected into a candidate whose family the pool no longer holds. The record is
    idempotent and global — a re-minted dead value is dropped without another fetch, and
    the record even spares a *different* campaign the probe. Best-effort: a provider
    outage leaves the value in the pool, since a timeout is not proof of zero support.
    Returns the number of values dropped. See ``p2-e3-discovery-empty-set-backoff``.
    """
    from openoutreach.core.models import Clause, EmptyClauseSet
    from openoutreach.core.pipeline import select
    from openoutreach.discovery import filters_for, search, step_line
    from openoutreach.emails import bettercontact

    known_empty = set(EmptyClauseSet.objects.values_list("clause_key", flat=True))

    dropped = 0
    for pair in new_pairs:
        pair = tuple(pair)

        # Proven dead on an earlier pass (records are global, keyed on the value alone)
        # — drop without a fetch.
        if select.clause_key([pair]) in known_empty:
            campaign.clauses.remove(*Clause.rows_for([pair]))
            dropped += 1
            continue

        try:
            rows = search(filters_for([pair]), limit=select.DISCOVERY_PAGE_SIZE, offset=0)
        except bettercontact.BetterContactUnavailable:
            continue  # can't fetch ≠ matches nobody — leave the value in the pool

        if rows:
            _harvest(campaign, [pair], 0, rows)
            continue

        select.record_empty([pair])
        campaign.clauses.remove(*Clause.rows_for([pair]))
        logger.info("%s", step_line(
            "pre-screen", "nothing in Lead Finder's index — value dropped from the pool",
            glyph="✗", color="yellow"))
        dropped += 1
    return dropped


def discover(session, qualifier) -> int:
    """Fetch one query's page and persist its first-touch Leads. Returns the count.

    Seeds and pre-screens the pool on a cold start, folds qualified learnings in on the
    throughput cadence (pre-screening the minted values), then fetches the GP's
    top-scored candidate. An empty page is recorded (its two shapes differently) and the
    next candidate tried — an offset-0 empty also backs off, so ``select`` offers the
    query's one-clause-looser generalizations next. The loop keeps firing the next-best
    query until one returns leads, so a run of dead queries never yields an empty-handed
    pass while any live candidate remains. Returns 0 only when the pool saturates
    (minting adds no surviving value) or a fetch is unavailable (best-effort — a
    provider outage must not fail the enclosing find_email task).

    Cost of never capping: on a mostly-dead ICP one call can fire many queries serially
    before it saturates, and each fetch is a blocking ~45s provider call. Termination
    still holds even though an empty now *spawns* generalizations: each empty iteration
    permanently records one distinct clause set (``next_query`` never re-picks a recorded
    empty), and the subset lattice is finite, so the recorded-empty set grows
    monotonically to a fixed bound and the loop ends at the non-empty frontier or at
    saturation.

    Gated as before: freemium campaigns seed from their kit, and a campaign with no
    finder key or no product/target can't be searched.
    """
    from openoutreach.core.conf import CAMPAIGN_CONFIG
    from openoutreach.core.pipeline import select
    from openoutreach.core.pipeline.icp import generate_seed
    from openoutreach.core.pipeline.mint import mint_clauses
    from openoutreach.discovery import filters_for, search, step_line
    from openoutreach.emails import bettercontact

    campaign = session.campaign
    if campaign.is_freemium:
        return 0
    if not bettercontact.is_configured():
        return 0
    if not (campaign.product_docs or campaign.campaign_target):
        return 0

    logger.info(colored(f"▶ discover · {campaign}", "blue", attrs=["bold"]))

    if not campaign.clauses.exists():
        _prescreen(campaign, generate_seed(campaign))

    # Throughput mint: fold in the leads that qualified since the last mint, then
    # pre-screen the fresh values so a dead axis never poisons a product slab.
    qualified = _qualified_count(campaign)
    if qualified and qualified - campaign.discovery_minted_at_qualified >= CAMPAIGN_CONFIG["mint_every_n_qualified"]:
        _prescreen(campaign, mint_clauses(campaign))

    empties = 0
    minted = False
    while True:
        query = select.next_query(campaign, qualifier)
        if query is None:
            # Saturation: the pool (with its backoff generalizations) spans nothing
            # fetchable. Widen the axes once — if a minted value survives the pre-screen
            # it opens a fresh maximal, so reselect; otherwise stop (the pool is bigger
            # now, so the next call retries). One mint per call bounds the loop when
            # every new value is either dead or empty-pruned.
            if not minted:
                fresh = mint_clauses(campaign)
                survivors = len(fresh) - _prescreen(campaign, fresh)
                if survivors > 0:
                    minted = True
                    continue
            logger.info(colored(
                f"■ discovery saturated · {campaign} — pool fully spanned "
                f"({empties} dead quer{'y' if empties == 1 else 'ies'} this pass)", "blue"))
            return 0

        filters = filters_for(query.clauses)
        try:
            rows = search(filters, limit=select.DISCOVERY_PAGE_SIZE, offset=query.offset)
        except bettercontact.BetterContactUnavailable as exc:
            # Best-effort: a provider outage or an un-fetchable query must not fail the
            # caller. Retire the query (persist so it isn't re-picked, exhaust so it
            # isn't deepened) but do NOT record it empty — we don't know it matches
            # nobody, only that we couldn't fetch it.
            logger.warning("%s", step_line(
                _move(query.offset), f"provider unavailable ({exc}) — retiring this query",
                glyph="⚠", color="red"))
            select.persist_fetched(campaign, query.clauses, query.offset)
            select.mark_exhausted(campaign, query.clauses)
            return 0

        if rows:
            created = _harvest(campaign, query.clauses, query.offset, rows)
            logger.info("%s", step_line(
                _move(query.offset), f"{created} new lead(s) from {len(rows)} row(s)",
                glyph="✓", color="green"))
            return created

        # Empty page — record what it means, then try the next candidate. offset 0
        # convicts the conjunction (matches nobody); a deeper empty is a vein run dry.
        if query.offset == 0:
            select.record_empty(query.clauses)
            logger.info("%s", step_line(
                "explore",
                "dead end — nobody matches this whole combination; recorded so any narrower "
                "query is pruned, backed off to its one-clause-looser generalizations",
                glyph="✗", color="yellow"))
        else:
            logger.info("%s", step_line(
                "deepen", f"vein empty — no more leads past offset {query.offset}; marked used up",
                glyph="✗", color="yellow"))
        select.persist_fetched(campaign, query.clauses, query.offset)
        select.mark_exhausted(campaign, query.clauses)
        empties += 1
        # No cap: loop back and try the next-best query. Each empty is now recorded +
        # exhausted, so next_query won't re-pick it and the candidate set shrinks —
        # the loop ends at saturation (next_query is None), not on a dead-query count.
