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

# The filter families a query may be composed from — every one **verified to
# steer** (2026-07-16: probed live against an unfiltered baseline, each with an
# absurd-value control). ``lead_industry``, ``company_technology`` and
# ``lead_skills`` are absent because they were proven inert: the page came back
# identical to the baseline, i.e. the filter was silently dropped.
#
# The field names are the contract; the values are search terms. An unknown *key*
# is dropped without a word and hands back the unfiltered page **with rows**, which
# reads as success — so keys are constrained here and in the pydantic schemas. An
# unknown *value* is benign: an empty page, one move spent.
FILTER_FAMILIES = (
    "company_headcount_min",
    "company_headcount_max",
    "lead_job_title",
    "lead_seniority",
    "lead_location",
    "lead_department",
    "lead_function",
)

# Families whose value is a bare scalar rather than an ``include`` list.
_SCALAR_FAMILIES = frozenset({"company_headcount_min", "company_headcount_max"})


def filters_for(clauses) -> dict:
    """``(family, value)`` clauses → one Lead Finder filter dict, ANDed across families.

    The inverse of the walk's model: a node is a set of clauses, at most one per
    family, and this is the only place that becomes provider JSON. Each family gets
    a **single-element** ``include`` list — an include-list of 5 titles is an OR, and
    an OR is strictly dominated (it compresses 5 sampling windows of ~10k rows into
    1); splitting it into 5 queries is free. See the roadmap card
    ``p2-e3-discovery-query-graph-search``.

    Raises ``ValueError`` on two clauses of the same family — that is the one-value-
    per-family invariant, enforced here because this dict is keyed by family and
    would otherwise let the second clause silently overwrite the first.
    """
    clauses = sorted(clauses)
    families = [family for family, _ in clauses]
    if len(families) != len(set(families)):
        raise ValueError(f"a query holds at most one clause per family, got {families}")

    filters: dict = {}
    for family, value in clauses:
        if family in _SCALAR_FAMILIES:
            filters[family] = int(value)
        elif family == "lead_job_title":
            filters[family] = {"include": [value], "exact_match": False}
        else:
            filters[family] = {"include": [value]}
    return filters

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


def describe_clauses(clauses) -> str:
    """``(family, value)`` clauses → ``"3 clauses: headcount 1–20 · title Founder"``.

    What a discovery move actually decided, in one line: how many clauses the query
    conjoins and which. Depth is the number worth seeing — a short conjunction
    matches millions and shows you only the provider's famous-company head, while a
    long one reaches the niche, so "how deep did we go" is the question a run log has
    to answer.

    The count is clauses, not rendered groups, so a headcount band reads as *one*
    ``headcount 1–20`` while counting *two* (``_min`` and ``_max`` are separate
    families to the provider, hence separate clauses). Depth is what the count is
    for; it is deliberately not the length of the text after the colon.
    """
    clauses = sorted(clauses)
    if not clauses:
        return "0 clauses"
    return f"{len(clauses)} clause(s): {describe_filters(filters_for(clauses))}"


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
    """Firmographic text for one lead row — the LLM qualifier's input, built from the
    fields that vary between leads.

    Absent fields are skipped rather than held as empty slots, so a sparse row
    stays short instead of padding out to the shape of a rich one. This is the LLM's
    input verbatim; the *embedding* adds the retrieving query's terms on top (see
    ``clause_terms``), which the LLM must not see or it would rubber-stamp a lead for
    matching the very query that found it.
    """
    return " ".join(str(row[f]) for f in TEXT_FIELDS if row.get(f)).lower()


def clause_terms(clauses) -> str:
    """Readable keyword text of a clause set, lowercased — the query as words.

    The single mechanism that puts queries and profiles in one embedding space: a
    discovered lead is embedded as ``profile_text + clause_terms(its retrieving
    query)``, and a *candidate* query is embedded as ``clause_terms`` alone. Sharing
    the values (``head of sales``, ``united states``) that also appear in profile
    text lets the GP — trained only on labelled profiles — score a never-run query by
    its keywords, and learn query-term → fit as a byproduct.
    """
    return describe_filters(filters_for(clauses)).lower()


def embed_profile(profile_text: str, query_terms: str = "") -> np.ndarray:
    """384-dim vector for a lead — its firmographic text plus its retrieving query's
    terms, so the GP learns which query keywords surface good leads."""
    from openoutreach.core.ml.embeddings import embed_text

    text = f"{profile_text} {query_terms}".strip()
    return embed_text(text)


def embed_query(clauses) -> np.ndarray:
    """384-dim vector for a candidate query — its keywords alone.

    Keyword-only, so a never-run query sits at the sparse edge of the labelled cloud:
    the GP is legitimately more uncertain there, which is exactly the explore signal
    an unsampled region should carry.
    """
    from openoutreach.core.ml.embeddings import embed_text

    return embed_text(clause_terms(clauses))
