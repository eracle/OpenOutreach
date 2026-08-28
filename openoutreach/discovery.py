# openoutreach/discovery.py
"""Lead discovery via BetterContact Lead Finder: search by ICP, embed the rows.

Discovery is free and returns no email — paid enrichment lives in
emails/bettercontact.py. Discovery blocks on the shared ``submit_and_poll``
transport; enrichment instead uses the non-blocking ``submit``/``poll_once``
split so the daemon never waits on a lookup.
"""
from __future__ import annotations

import logging
from typing import Literal, NamedTuple, get_args

import numpy as np
from termcolor import colored

from openoutreach.core.logblock import step_line  # noqa: F401 (re-exported for callers)
from openoutreach.enrichment.bettercontact import submit_and_poll

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

# The fields a query node may add keywords to — the search axes.
#
# Every one is **verified to steer** (2026-07-16, re-confirmed 2026-07-28 with a
# nonsense control). Three fields that used to be here are gone, each for its own
# measured reason — see the roadmap card
# ``p1-e3-leadfinder-index-semantics-and-query-model-rethink``:
#
# - ``lead_industry`` — inert. ``zzqqxxnonsense`` returns the *identical* count to
#   no filter at all, so the field is silently dropped (§8).
# - ``lead_function`` — not a second field. It and ``lead_department`` are one
#   field reachable under two names, and their values are merged into a single
#   ORed include-list, so naming both *widens* the query (§2).
# - ``lead_department`` — real, but unreachable from a profile. Lead Finder emits
#   no department on a lead row, so no vocabulary can ever grow for it, and its
#   whole live vocabulary is four values nobody would guess (no Engineering, no
#   Finance — §3). An axis with no token source is dead weight.
#
# The field names are the contract; the tokens are search terms. An unknown *key*
# is dropped without a word and hands back the unfiltered page **with rows**, which
# reads as success — so keys are constrained here and in the pydantic schemas. An
# unknown *value* is benign: an empty page, one move spent.
SEARCH_FIELDS = (
    "lead_job_title",
    "lead_seniority",
    "lead_location",
)


def filters_for(keywords, headcount: tuple[int, int] | None = None) -> dict:
    """``(field, token)`` keywords → one Lead Finder filter dict.

    The one place a node becomes provider JSON, and the only place the index's three
    operators are chosen between (§1, §6 of the card):

    - **words inside one string AND.** Tokens landing in the same field are joined
      with a space, so ``{("lead_job_title", "founder"), ("lead_job_title", "cto")}``
      queries ``"founder cto"`` — every token must be present. This is the walk's
      narrowing move, and it is the *generator* of the best queries measured
      (``"founder cto"`` counts 9,027 at near-perfect precision, §10).
    - **field vs field ANDs.** Separate keys, as before.
    - **strings inside one list OR** — deliberately unused. A union of values reaches
      one ~10k window where the same values as separate queries reach one each, so OR
      is strictly dominated for harvesting (§7).

    ``headcount`` is the campaign's fixed ICP size band, riding every query
    unchanged — it is never a search axis, because loosening a size bound queries
    off-ICP rather than widening usefully.
    """
    grouped: dict[str, list[str]] = {}
    for field, token in sorted(keywords):
        grouped.setdefault(field, []).append(token)

    filters: dict = {}
    if headcount is not None:
        filters["company_headcount_min"] = int(headcount[0])
        filters["company_headcount_max"] = int(headcount[1])

    for field, tokens in grouped.items():
        joined = " ".join(tokens)
        if field == "lead_job_title":
            filters[field] = {"include": [joined], "exact_match": False}
        else:
            filters[field] = {"include": [joined]}
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


# Which lead-row fields supply the vocabulary for each search axis. The token a
# profile contributes has to land in a field the provider will actually match it in:
# ``cto`` is alive in ``lead_job_title`` and dead in every other, ``belgium`` the
# reverse. Reading each axis from the row fields that *are* that axis keeps the two
# vocabularies nearly disjoint for free, so the walk rarely has to discover by
# fetching that a token is in the wrong place.
#
# ``lead_seniority`` is not sourced from rows at all — it is a closed 12-value
# vocabulary the provider publishes (``Seniority``), so it is seeded whole and never
# grown. There is no ``lead_department`` source because no lead row carries one.
#
# ``contact_headline`` used to feed ``lead_job_title`` and does not any more. A headline
# is marketing prose — "Building AI-powered agents for users" — and its words repeat
# across people, so they cleared the df≥2 floor and were filed as job titles: a live
# campaign grew ``user``, ``agents``, ``agile``, ``shipping`` and ``ai-powered`` as title
# tokens. Conjoined, they ask for a title nobody holds. Only ``contact_job_title`` says
# what the provider will match in that field.
KEYWORD_SOURCE_FIELDS = {
    "lead_job_title": ("contact_job_title",),
    "lead_location": ("contact_location_state", "contact_location_country"),
}


def source_fields_for(row: dict) -> dict:
    """The row's queryable text, kept per field for the vocabulary.

    Only the fields ``KEYWORD_SOURCE_FIELDS`` reads — a lead row carries far more, and
    storing the rest would be a second copy of ``profile_text`` under another name.
    """
    wanted = {key for keys in KEYWORD_SOURCE_FIELDS.values() for key in keys}
    return {key: str(row[key]) for key in wanted if row.get(key)}


# Who the row is about, as opposed to the firmographic *text* the qualifier reads.
# Verified against a live Lead Finder response (2026-06-30 probe, 0 credits): discovery
# reports one ``contact_full_name``, never a first/last pair — those arrive later, from
# the paid enrichment leg's own response (``emails/bettercontact.py``), so nothing here
# ever splits a name.
#
# Stored as named ``Lead`` columns rather than folded into ``profile_text`` because the
# export hands them to another tool as *fields*, and because they are the record the
# product keeps. Deliberately out of the embedding — a person's name carries no ICP
# signal, and vectorising it would give the GP noise to learn on.
PERSON_FIELDS = {
    "full_name": "contact_full_name",
    "job_title": "contact_job_title",
}

# The employer, which becomes a ``crm.Company`` row rather than two columns on the
# Lead — many leads in one ICP share an employer, and the company record is worth
# keeping in its own right.
#
# Handle with care downstream: the provider *fuzzy-matches* this record (see the
# ``TEXT_FIELDS`` note above — a boutique law firm's founder comes back as Meta), so a
# Company row is what the provider said, not verified truth.
COMPANY_FIELDS = {
    "name": "company_name",
    "domain": "company_domain",
}


def person_for(row: dict) -> dict:
    """The row's person fields as ``Lead`` column values, ``None`` where unreported."""
    return {field: _clean(row.get(key)) for field, key in PERSON_FIELDS.items()}


def company_for(row: dict):
    """The row's employer as a ``crm.Company``, created on first sight. ``None`` if unnamed."""
    from openoutreach.crm.models import Company

    return Company.from_row(**{field: _clean(row.get(key))
                               for field, key in COMPANY_FIELDS.items()})


def _clean(value) -> str | None:
    """A provider string, or ``None`` — which is what "they didn't tell us" means."""
    text = str(value).strip() if value is not None else ""
    return text or None


def describe_node(keywords) -> str:
    """``(field, token)`` keywords → ``"2 keyword(s): title founder cto"``.

    What a discovery move actually decided, in one line: how many tokens the query
    ANDs and which. Depth is the number worth seeing — one token matches millions and
    shows you only the provider's famous-company head, while three reach the niche, so
    "how deep did we go" is the question a run log has to answer. The band is omitted:
    it rides every node identically and says nothing about the move.
    """
    keywords = sorted(keywords)
    if not keywords:
        return "0 keywords (everyone)"
    return f"{len(keywords)} keyword(s): {describe_filters(filters_for(keywords))}"


def describe_filters(filters: dict) -> str:
    """One-line human rendering of a Lead Finder filter set, for logs.

    Filter sets are nested (``{"lead_job_title": {"include": [...], "exact_match":
    false}}``) and read as machinery in a log line, which is the only place they
    appear. Renders the two headcount bounds as the one range they describe, drops
    the ``lead_`` prefix and the ``include`` wrapper, and keeps ``exact_match``
    since it changes what the query matches. (The only ``company_*`` keys are the
    two headcount bounds, both consumed by the range above.)
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
        label = key.removeprefix("lead_")
        if isinstance(value, dict):
            rendered = ", ".join(str(v) for v in value.get("include", [])) or "(none)"
            if value.get("exact_match"):
                rendered += " (exact)"
        else:
            rendered = str(value)
        parts.append(f"{label} {rendered}")
    return " · ".join(parts)


# ── Discovery-walk log block ──
# One query renders as one block: a ``▸`` header naming the query, then indented
# step lines rendered through the shared ``logblock`` grammar (see that module) —
# the provider round-trip, then the walk's verdict. The clause set is printed once,
# in the header, and never repeated.


def query_header(filters: dict, offset: int = 0) -> str:
    """The ``▸`` header line naming the query a block reports on."""
    line = colored("▸ ", "cyan", attrs=["bold"]) + colored(describe_filters(filters), "cyan")
    return line + (f"  · offset {offset}" if offset else "")


class Page(NamedTuple):
    """One Lead Finder response: the rows, and the corpus count behind them.

    ``leads_found`` is the provider's exact count for the query — free, in the same
    call, and **only meaningful at offset 0**: past the end of *any* result set the
    API reports ``0``, at 10,100 for a huge query and at 500 for a 397-row one (§7).
    ``None`` here means "not asked at offset 0, so unknown", never "zero".
    """

    leads: list[dict]
    leads_found: int | None


def search(filters: dict, limit: int = 100, offset: int = 0) -> Page:
    """Search Lead Finder by ICP filters; return the matching rows and the corpus count.

    Logs the outgoing query before the call: an unknown filter key or value is
    answered with an empty page rather than an error, so the query itself is the
    only evidence of why a line came back dry — and the call blocks, so logging
    it after would lose it to a timeout.

    The count lives in ``summary.leads_found`` and used to be discarded. It is the
    one signal that separates a genuinely empty query from a transport artifact: a
    burst of calls can answer a 71-million-lead query with an empty page in 0.0s
    (§4), and writing that down as "matches nobody" is how the old walk blacklisted
    good queries permanently.
    """
    from openoutreach.core.models import SiteConfig

    api_key = SiteConfig.load().bettercontact_api_key
    body = {"filters": filters, "limit": limit, "offset": offset}
    logger.info("%s", query_header(filters, offset))
    result = submit_and_poll(api_key, LEAD_FINDER_URL, body)

    leads = result.get("leads", [])
    summary = result.get("summary") or {}
    found = summary.get("leads_found") if offset == 0 else None
    detail = f"{len(leads)} leads" + (f" · {found:,} in the index" if found else "")
    logger.info("%s", step_line("leadfinder", detail))
    # A zero is the one answer worth seeing whole. §4 measured the provider handing back
    # an empty page for a 71-million-lead query under burst, so when rows come back empty
    # the raw summary and status are the evidence for whether that is what happened.
    if not leads:
        logger.debug("    raw response: status=%s summary=%s credits_left=%s",
                     result.get("status"), summary, result.get("credits_left"))
    return Page(leads, found)


def profile_text_for(row: dict) -> str:
    """Firmographic text for one lead row — the LLM qualifier's input, built from the
    fields that vary between leads.

    Absent fields are skipped rather than held as empty slots, so a sparse row
    stays short instead of padding out to the shape of a rich one. This is the LLM's
    input verbatim; the *embedding* adds the retrieving query's terms on top (see
    ``keyword_terms``), which the LLM must not see or it would rubber-stamp a lead for
    matching the very query that found it.
    """
    return " ".join(str(row[f]) for f in TEXT_FIELDS if row.get(f)).lower()


def keyword_terms(keywords) -> str:
    """A node's tokens as plain lowercase words — the query as text.

    Folded into a discovered lead's **embedding only**, never into ``profile_text``,
    so the LLM judges the person on firmographics while the vector still carries which
    query surfaced them.

    This used to be load-bearing: it put queries and profiles in one embedding space so
    the GP could score a never-run query by its keywords. That job is gone — §13
    measured the GP losing to plain counting as a frontier ranker, and query selection
    is pure arithmetic now. What keeps the injection is the vector space itself: every
    cached ``Lead.embedding`` was built this way, and dropping the terms would silently
    move new leads into a different space from the 14k already stored.
    """
    return " ".join(token for _, token in sorted(keywords)).lower()


def embed_profile(profile_text: str, query_terms: str = "") -> np.ndarray:
    """384-dim vector for a lead — its firmographic text plus its retrieving query's
    terms, so the GP learns which query keywords surface good leads."""
    from openoutreach.core.ml.embeddings import embed_text

    text = f"{profile_text} {query_terms}".strip()
    return embed_text(text)


# ``embed_query``/``embed_queries`` used to live here — 384-dim vectors for a
# *candidate query*, so the GP could rank the frontier by keyword embedding. Both are
# gone: §13 measured that ranking against plain counting and the counts won outright
# (0.661 vs 0.450), with the GP adding nothing on top of them. The frontier is scored
# by arithmetic over labels now, and nothing embeds a query.
