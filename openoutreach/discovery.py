# openoutreach/discovery.py
"""Lead discovery via BetterContact Lead Finder: search by ICP, embed the rows.

Discovery is free and returns no email — paid enrichment lives in
emails/bettercontact.py. Discovery blocks on the shared ``submit_and_poll``
transport; enrichment instead uses the non-blocking ``submit``/``poll_once``
split so the daemon never waits on a lookup.
"""
from __future__ import annotations

import logging
from typing import Literal, get_args

import numpy as np
from termcolor import colored

from openoutreach.emails.bettercontact import submit_and_poll

logger = logging.getLogger(__name__)

LEAD_FINDER_URL = "https://app.bettercontact.rocks/api/v2/lead_finder/async"

# Lead Finder's `lead_seniority` vocabulary — the only values it matches. An
# unknown value (or wrong casing) is **not** an error: the API returns an empty
# page, which the frontier reads as end-of-depth and marks the query exhausted.
# So a bad value silently kills a query line. Typed as a Literal so the LLM's
# structured output *cannot* emit an invalid level (pydantic rejects and retries)
# — prose in the prompt is guidance, this is enforcement. ``LEAD_SENIORITIES``
# derives from it, keeping schema and prompt from ever drifting apart.
Seniority = Literal[
    "owner", "founder", "c_suite", "partner", "vp", "head",
    "director", "manager", "senior", "mid-level", "entry", "intern",
]
LEAD_SENIORITIES = get_args(Seniority)

# Lead-row fields we embed, folded in only when the row carries them.
#
# Every field here must *vary between leads*: the GP's whole job is to rank the
# candidates in a pool against each other, and a field that is constant across
# them cannot contribute to that ranking however accurate it is. That test is
# what excludes the ``company_*`` free text. Lead Finder staples a fuzzy-matched
# company record onto each row — a boutique law firm's founder comes back as
# Meta, mission statement and all — and a 100-row page carries 1–4 distinct
# company records. ``company_description`` (59% of the text) and
# ``company_keywords`` (21%) were therefore 80% of every vector at ~zero bits.
# ``contact_location`` is absent from every response and was always an empty slot.
#
# ``contact_headline`` is the one field with real per-lead signal (54 distinct
# values per 100 rows) and is present for barely half of them; the rest are short
# categoricals. Dropping a field moves the vector space, so every ``Lead`` must be
# re-embedded when this list changes.
TEXT_FIELDS = [
    "contact_headline",
    "contact_industry",
    "contact_job_title",
    "company_name",
    "contact_seniority",
    "company_industry",
    "contact_location_state",
    "contact_location_country",
]


def describe_filters(filters: dict) -> str:
    """One-line human rendering of a Lead Finder filter set, for logs.

    Filter sets are nested (``{"lead_job_title": {"include": [...], "exact_match":
    false}}``) and read as machinery in a log line, which is the only place they
    appear. Renders the two headcount bounds as the one range they describe, drops
    the ``lead_``/``company_`` prefixes and the ``include`` wrapper, and keeps
    ``exact_match`` since it changes what the query matches.
    """
    if not filters:
        return "(no filters)"

    low, high = filters.get("company_headcount_min"), filters.get("company_headcount_max")
    parts = []
    if low is not None or high is not None:
        parts.append(f"headcount {low if low is not None else '?'}–"
                     f"{high if high is not None else '?'}")

    for key, value in filters.items():
        if key in ("company_headcount_min", "company_headcount_max"):
            continue
        label = key.removeprefix("lead_").removeprefix("company_")
        if isinstance(value, dict):
            rendered = ", ".join(str(v) for v in value.get("include", [])) or "(none)"
            if value.get("exact_match"):
                rendered += " (exact)"
        else:
            rendered = str(value)
        parts.append(f"{label} {rendered}")
    return " · ".join(parts)


def search(filters: dict, limit: int = 100, offset: int = 0) -> list[dict]:
    """Search Lead Finder by ICP filters; return the matching lead rows.

    Logs the outgoing query before the call: an unknown filter key or value is
    answered with an empty page rather than an error, so the query itself is the
    only evidence of why a line came back dry — and the call blocks, so logging
    it after would lose it to a timeout.
    """
    from openoutreach.core.models import SiteConfig

    api_key = SiteConfig.load().bettercontact_api_key
    body = {"filters": filters, "limit": limit, "offset": offset}
    logger.info("leadfinder query: %s (limit %d, offset %d)",
                colored(describe_filters(filters), "cyan"), limit, offset)
    result = submit_and_poll(api_key, LEAD_FINDER_URL, body)

    leads = result.get("leads", [])
    logger.info("leadfinder: %d lead(s) returned", len(leads))
    return leads


def profile_text_for(row: dict) -> str:
    """Firmographic text for one lead row — the LLM qualifier's input and the
    embedding's source, built from the same fields so both stay comparable.

    Absent fields are skipped rather than held as empty slots, so a sparse row
    stays short instead of padding out to the shape of a rich one.
    """
    return " ".join(str(row[f]) for f in TEXT_FIELDS if row.get(f)).lower()


def embed_row(row: dict) -> np.ndarray:
    """384-dim vector for one lead row, for ML qualification."""
    from openoutreach.core.ml.embeddings import embed_text

    return embed_text(profile_text_for(row))
