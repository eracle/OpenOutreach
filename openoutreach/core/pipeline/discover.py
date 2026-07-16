# openoutreach/core/pipeline/discover.py
"""Discovery leg — one best-first move over the campaign's query walk.

The top of the funnel: a page of ICP-matched rows becomes ``Lead`` rows (embedded
+ profile_text) awaiting qualification. Free (Lead Finder bills nothing) and
browserless. The qualify chain calls ``discover`` whenever its candidate pool runs
dry; each call fetches the single most promising query — the deeper page of a
productive vein, or a fresh region when the current ones stop paying — so
successive calls steer toward the region of the query space that feeds
qualification best. See ``frontier.py`` and the roadmap card
``p2-e3-discovery-query-graph-search``.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

DISCOVERY_PAGE_SIZE = 100


def discover(session, qualifier) -> int:
    """Fetch one query's page and persist its first-touch Leads. Returns the count.

    One move: re-rank the fetched nodes against the current GP, then ask the
    frontier for the next query (generating the ICP seed on a cold start) and fetch
    its Lead Finder page. A page with rows is persisted (leads first-touch-stamped
    via ``Lead.discovered_by``) and the count returned. An **empty page** marks
    that ``params`` exhausted and the move
    retries the next-best query — deepening drains a finite set of veins, and a
    freshly walled region that comes back empty ends the move.

    Returns 0 only when the whole walk is dry (no query left to fetch), never when
    a single branch ends — so the qualify chain's "``discover() <= 0`` means
    exhausted" contract still holds.

    Gated as before: freemium campaigns seed from their kit (not Lead Finder),
    and a campaign with no finder key or no product/target can't be searched.
    """
    from openoutreach.core.db.leads import create_lead
    from openoutreach.core.pipeline import frontier
    from openoutreach.discovery import describe_filters, search
    from openoutreach.emails import bettercontact

    campaign = session.campaign
    if campaign.is_freemium:
        return 0
    if not bettercontact.is_configured():
        return 0
    if not (campaign.product_docs or campaign.campaign_target):
        return 0

    frontier.rerank(campaign, qualifier)

    walled = False
    while True:
        query = frontier.next_query(campaign, qualifier)
        if query is None:
            return 0
        if query.move == "wall" and walled:
            return 0  # one new region per move — its emptiness ends this move

        rows = search(query.params, limit=DISCOVERY_PAGE_SIZE, offset=query.offset)
        if rows:
            node = frontier.persist_fetched(campaign, query.params, query.offset)
            created = sum(
                create_lead(row, country_code=campaign.country_code, discovered_by=node)
                for row in rows
            )
            logger.info("[%s] discovery: %d new lead(s) from %d row(s) (%s, offset %d) — %s",
                        campaign, created, len(rows), query.move, query.offset,
                        describe_filters(query.params))
            return created

        # Empty page — record the dry attempt and exhaust its line, then retry.
        # Neither shape of empty is an error: a region that holds no leads is a
        # real answer, and widening past it is the walk working. Offset 0 still
        # tells them apart when reading a run back — the query matched nothing at
        # all, versus a vein that finally ran out.
        if query.offset == 0:
            logger.info(
                "[%s] discovery: %s query matched nothing — exhausting %s "
                "(an empty region and a filter value Lead Finder doesn't know "
                "look identical here)",
                campaign, query.move, describe_filters(query.params),
            )
        else:
            logger.info("[%s] discovery: %s vein ran dry at offset %d — exhausting %s",
                        campaign, query.move, query.offset,
                        describe_filters(query.params))
        frontier.persist_fetched(campaign, query.params, query.offset)
        frontier.mark_exhausted(campaign, query.params)
        if query.move == "wall":
            walled = True
