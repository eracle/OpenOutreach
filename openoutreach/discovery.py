# openoutreach/discovery.py
"""Lead discovery via BetterContact Lead Finder: search by ICP, embed the rows.

Discovery is free and returns no email — paid enrichment lives in
emails/bettercontact.py, whose submit_and_poll transport both calls share.
"""
from __future__ import annotations

import logging

import numpy as np

from openoutreach.emails.bettercontact import submit_and_poll

logger = logging.getLogger(__name__)

LEAD_FINDER_URL = "https://app.bettercontact.rocks/api/v2/lead_finder/async"

# Lead-row fields we embed, in the same order as the LinkedIn-era
# build_profile_text so old and new vectors stay comparable.
TEXT_FIELDS = [
    "contact_headline",
    "contact_location",
    "contact_industry",
    "contact_job_title",
    "company_name",
    "company_description",
]


def search(filters: dict, limit: int = 100, offset: int = 0) -> list[dict]:
    """Search Lead Finder by ICP filters; return the matching lead rows."""
    from openoutreach.core.models import SiteConfig

    api_key = SiteConfig.load().bettercontact_api_key
    body = {"filters": filters, "limit": limit, "offset": offset}
    result = submit_and_poll(api_key, LEAD_FINDER_URL, body)

    leads = result.get("leads", [])
    logger.info("leadfinder: %d lead(s) returned", len(leads))
    return leads


def embed_row(row: dict) -> np.ndarray:
    """384-dim vector for one lead row, for ML qualification."""
    from openoutreach.core.ml.embeddings import embed_text

    text = " ".join(row.get(f) or "" for f in TEXT_FIELDS).lower()
    return embed_text(text)
