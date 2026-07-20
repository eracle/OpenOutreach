# openoutreach/core/pipeline/discover.py
"""Discovery leg — one best-first move over the campaign's query walk.

The top of the funnel: a page of ICP-matched rows becomes ``Lead`` rows (embedded
+ profile_text) awaiting qualification. Free (Lead Finder bills nothing) and
browserless. The qualify chain calls ``discover`` whenever its candidate pool runs
dry; each call fetches the single most promising query — the deeper page of a
productive vein, or a fresh region when the current ones stop paying — so
successive calls steer toward the region of the query space that feeds
qualification best.

The move takes no qualifier: what a query region is worth is measured from the
deals its leads earned, not predicted by the GP. See ``frontier.py`` and the roadmap
card ``p2-e3-discovery-query-graph-search``.
"""
from __future__ import annotations

import logging

from termcolor import colored

logger = logging.getLogger(__name__)

# One colour per move, so a run reads at a glance: green is mining a vein that
# pays, yellow is widening blind into a region nothing has been ruled on yet.
_MOVE_COLORS = {"deepen": "green", "visit": "yellow"}


def _move(name: str) -> str:
    """The move name in its colour."""
    return colored(name, _MOVE_COLORS.get(name, "white"), attrs=["bold"])


def discover(session) -> int:
    """Fetch one query's page and persist its first-touch Leads. Returns the count.

    One move: ask the frontier for the next query (seeding the ICP's clause pool on a
    cold start) and fetch its Lead Finder page. A page with rows is persisted (leads
    first-touch-stamped via ``Lead.discovered_by``) and the count returned.

    **This is the leg that learns.** There is no cheap liveness probe anywhere in the
    walk — a fetch is how the lattice finds out whether a conjunction holds anybody,
    so the two shapes of empty page are recorded differently here and nowhere else:

    - **offset 0** — the conjunction matches nobody. ``record_empty`` blacklists it,
      which prunes every superset of it for free, and the line is exhausted.
    - **offset > 0** — a vein ran out. Exhaust the line and nothing more: the query
      matched people, we have simply seen them all.

    Returns 0 when the whole walk is dry (no query left to fetch) — so the qualify
    chain's "``discover() <= 0`` means exhausted" contract holds — and also when a
    discovery fetch is unavailable: a provider outage or timeout is best-effort, so it
    retires that one query and ends the move rather than failing the caller.

    Gated as before: freemium campaigns seed from their kit (not Lead Finder),
    and a campaign with no finder key or no product/target can't be searched.
    """
    from openoutreach.core.db.leads import create_lead
    from openoutreach.core.pipeline import frontier
    from openoutreach.core.pipeline.frontier import DISCOVERY_PAGE_SIZE
    from openoutreach.discovery import describe_clauses, filters_for, search
    from openoutreach.emails import bettercontact

    campaign = session.campaign
    if campaign.is_freemium:
        return 0
    if not bettercontact.is_configured():
        return 0
    if not (campaign.product_docs or campaign.campaign_target):
        return 0

    logger.info(colored("▶ discover", "blue", attrs=["bold"]))

    visited = False
    while True:
        query = frontier.next_query(campaign)
        if query is None:
            return 0
        if query.move == "visit" and visited:
            # One fresh region per move. Without a probe every candidate costs a real
            # ~45s fetch, so a run of empty conjunctions would otherwise grind through
            # the lattice inside a single call; the daemon's next move picks up where
            # this left off, with the blacklist already one entry richer.
            return 0

        filters = filters_for(query.clauses)
        try:
            rows = search(filters, limit=DISCOVERY_PAGE_SIZE, offset=query.offset)
        except bettercontact.BetterContactUnavailable as exc:
            # Discovery is best-effort: a provider outage or a query the provider
            # can't answer in time must not fail the caller (this runs inside a
            # find_email task in cold start). Retire the query — persist it so the
            # visit won't re-pick it, and exhaust it so deepen won't — but do NOT
            # record it empty: we don't know it matches nobody, only that we couldn't
            # fetch it. The walk moves to the next query on the next move.
            logger.warning("[%s] %s: discovery fetch of %s unavailable (%s) — retiring it",
                           campaign, _move(query.move),
                           colored(describe_clauses(query.clauses), "cyan"), exc)
            frontier.persist_fetched(campaign, query.clauses, query.offset)
            frontier.mark_exhausted(campaign, query.clauses)
            return 0
        if rows:
            node = frontier.persist_fetched(campaign, query.clauses, query.offset)
            created = sum(
                create_lead(row, country_code=campaign.country_code, discovered_by=node)
                for row in rows
            )
            logger.info("[%s] %s: %s new lead(s) from %d row(s) (offset %d) — %s",
                        campaign, _move(query.move),
                        colored(str(created), "green", attrs=["bold"]),
                        len(rows), query.offset,
                        colored(describe_clauses(query.clauses), "cyan"))
            return created

        # Empty page — record what it means and exhaust the line, then retry. Neither
        # shape of empty is an error: a region that holds no leads is a real answer,
        # and widening past it is the walk working. Only offset 0 convicts the
        # conjunction, and it convicts nothing smaller than the whole set.
        if query.offset == 0:
            frontier.record_empty(query.clauses)
            logger.info(
                "[%s] %s: query matched %s — blacklisting %s "
                "(an empty region and a filter value Lead Finder doesn't know "
                "look identical here)",
                campaign, _move(query.move), colored("nothing", "yellow", attrs=["bold"]),
                colored(describe_clauses(query.clauses), "cyan"),
            )
        else:
            logger.info("[%s] %s: vein %s at offset %d — exhausting %s",
                        campaign, _move(query.move),
                        colored("ran dry", "yellow", attrs=["bold"]), query.offset,
                        colored(describe_clauses(query.clauses), "cyan"))
        frontier.persist_fetched(campaign, query.clauses, query.offset)
        frontier.mark_exhausted(campaign, query.clauses)
        if query.move == "visit":
            visited = True
