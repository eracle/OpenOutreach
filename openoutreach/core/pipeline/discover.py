# openoutreach/core/pipeline/discover.py
"""Discovery leg — pull Lead Finder rows for a campaign's ICP into embedded Leads.

The top of the funnel: a page of ICP-matched rows becomes ``Lead`` rows (embedded
+ profile_text) awaiting qualification. Free (Lead Finder bills nothing for
discovery) and browserless. The qualify chain calls this when its candidate pool
runs dry; the persistent ``Campaign.discovery_offset`` cursor advances page by
page so re-runs bring *new* leads rather than re-fetching page 1.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

DISCOVERY_PAGE_SIZE = 100


def discover(session) -> int:
    """Fetch the next ICP page and persist new Leads. Returns the count created.

    Gated: freemium campaigns seed from their kit (not Lead Finder); a campaign
    with no finder key or no product/target can't be searched. A dry page
    (offset past the result set) returns 0 and stops the qualify chain.
    """
    from openoutreach.core.db.leads import create_lead
    from openoutreach.core.pipeline.icp import icp_for
    from openoutreach.discovery import search
    from openoutreach.emails import bettercontact

    campaign = session.campaign
    if campaign.is_freemium:
        return 0
    if not bettercontact.is_configured():
        return 0
    if not (campaign.product_docs or campaign.campaign_target):
        return 0

    spec = icp_for(campaign)
    filters = spec.get("filters") or {}
    if not filters:
        return 0

    rows = search(filters, limit=DISCOVERY_PAGE_SIZE, offset=campaign.discovery_offset)
    if not rows:
        return 0

    country_code = spec.get("country_code", "")
    created = sum(create_lead(row, country_code=country_code) for row in rows)

    campaign.discovery_offset += len(rows)
    campaign.save(update_fields=["discovery_offset"])
    logger.info("[%s] discovery: %d new lead(s) from %d row(s) (offset now %d)",
                campaign, created, len(rows), campaign.discovery_offset)
    return created
