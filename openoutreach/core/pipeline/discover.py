# openoutreach/core/pipeline/discover.py
"""Discovery leg — one best-first move over the campaign's query frontier.

The top of the funnel: a page of ICP-matched rows becomes ``Lead`` rows (embedded
+ profile_text) awaiting qualification. Free (Lead Finder bills nothing) and
browserless. The qualify chain calls ``discover`` whenever its candidate pool runs
dry; each call fetches the single most promising query node on the frontier — not
a fixed cursor — so successive calls steer toward the region of the query graph
that feeds qualification best. See ``frontier.py`` and the roadmap card
``p2-e3-discovery-query-graph-search``.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

DISCOVERY_PAGE_SIZE = 100


def discover(session, qualifier) -> int:
    """Fetch one frontier node's page and persist its first-touch Leads.

    One move: re-rank the fetched nodes against the current GP, pick the next
    PENDING node, fetch its Lead Finder page, persist new Leads (first-touch
    stamped via ``Lead.discovered_by``), then expand the node into new PENDING
    children. Returns the count of Leads created.

    Dry nodes are retired and skipped within the call, so the qualify chain's
    "``discover() <= 0`` means exhausted" contract still holds — 0 is returned
    only when the whole frontier is dry, never when a single branch ends.

    Gated as before: freemium campaigns seed from their kit (not Lead Finder),
    and a campaign with no finder key or no product/target can't be searched.
    """
    from openoutreach.core.db.leads import create_lead
    from openoutreach.core.pipeline import frontier
    from openoutreach.discovery import search
    from openoutreach.emails import bettercontact

    campaign = session.campaign
    if campaign.is_freemium:
        return 0
    if not bettercontact.is_configured():
        return 0
    if not (campaign.product_docs or campaign.campaign_target):
        return 0

    frontier.ensure_seed(campaign)
    frontier.rerank(campaign, qualifier)

    while True:
        node = frontier.pick(campaign, qualifier)
        if node is None:
            return 0

        rows = search(node.params, limit=DISCOVERY_PAGE_SIZE, offset=node.offset)
        if not rows:
            frontier.retire(node)
            continue

        created = sum(
            create_lead(row, country_code=campaign.country_code, discovered_by=node)
            for row in rows
        )
        frontier.mark_fetched(node, qualifier)
        frontier.expand(campaign, node, qualifier)
        frontier.enforce_size_cap(campaign)
        logger.info("[%s] discovery: %d new lead(s) from %d row(s) (node #%d, offset %d)",
                    campaign, created, len(rows), node.pk, node.offset)
        return created
