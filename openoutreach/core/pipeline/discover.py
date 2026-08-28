# openoutreach/core/pipeline/discover.py
"""Discovery leg — fire the frontier's best node into first-touch Leads.

The top of the funnel: a page of ICP-matched rows becomes ``Lead`` rows awaiting
qualification. Free (Lead Finder bills nothing, for counting calls *and* paged ones) and
browserless, so wall-clock is the only budget — roughly 45s per page.

One pass is: make sure there is a vocabulary and a frontier, draw a node
(``select.next_node``), page it, and act on what came back. A page with rows is harvested,
the node's offset advances and its children join the frontier. An empty page retires the
node and the loop tries the next one, so a run of dead queries never yields an
empty-handed pass while any live candidate remains.

**Empty is three different facts and they are not interchangeable** — the provider reports
``0`` for all of them (§7 of the roadmap card). ``search`` now also returns
``summary.leads_found``, which separates the fourth case: rows empty while the count is
positive is a *transport artifact*, not an answer. A burst of calls can hand back an empty
page for a 71-million-lead query in 0.0s (§4), and the old walk wrote that down as
"matches nobody" — permanently, and for every campaign. That case is caught by the count,
not by asking twice: an empty page carrying a positive ``leads_found`` never retires
anything, and an empty page with a zero count is believed on the spot.

There is no pre-screen, no clause minting, no LLM in the loop at all past the cold-start
seed: the vocabulary is counted from qualified profiles (``vocabulary.refresh``) and the
frontier is ranked by counting (``select``).
"""
from __future__ import annotations

import logging

from termcolor import colored

logger = logging.getLogger(__name__)


def _harvest(campaign, node, rows: list[dict]) -> int:
    """Persist a fetched page as first-touch Leads, keyworded by the retrieving node.

    Returns the count of leads newly created; a re-surfaced profile keeps its original
    ``discovered_by``. A page of entirely-familiar profiles therefore returns 0 while
    still being a perfectly good page — which is why the caller does not read this as
    "nothing left here" (that was bug 8: a full page of duplicates halting the engine with
    the frontier wide open).
    """
    from openoutreach.core.db.leads import create_lead
    from openoutreach.discovery import keyword_terms

    pairs = node.pairs
    terms = keyword_terms(pairs)
    return sum(
        create_lead(row, country_code=campaign.country_code,
                    discovered_by=node, query_terms=terms)
        for row in rows
    )


def _ensure_frontier(campaign, store) -> list[tuple[str, str]]:
    """Make sure the campaign has a vocabulary and something to fire. Returns the vocabulary.

    Cold start seeds from the ICP; every pass folds in the words of whatever has qualified
    since. Both are cheap and idempotent — counting, not generation — so there is no
    cadence to trigger and no high-water mark to store.
    """
    from openoutreach.core.models import QueryNode
    from openoutreach.core.pipeline import select, vocabulary
    from openoutreach.core.pipeline.icp import generate_seed

    vocabulary.seed_seniorities()
    existing = QueryNode.objects.filter(campaign=campaign).exists()
    if not existing:
        generate_seed(campaign)
    vocabulary.refresh(campaign)

    keywords = vocabulary.admitted_keywords()
    if not keywords:
        return keywords

    if not existing:
        opened = select.seed_frontier(campaign, keywords)
        logger.debug("[%s] frontier opened with %d keyword(s)", campaign, opened)
        return keywords

    # The vocabulary grew since the last pass — a new token is only ever a *child* of an
    # already-fired node, so re-expanding those is what lets it reach the frontier at all.
    for node in QueryNode.objects.filter(
        campaign=campaign, state=QueryNode.State.FIRED,
    ).prefetch_related("keywords"):
        select.expand(node, store, keywords)
    return keywords


def _fetch(node, offset: int):
    """One page, or ``None`` when the provider could not be reached.

    An outage is explicitly *not* evidence about the query. The old walk called
    ``mark_exhausted`` here, which was final and had no retry path, so one hiccup during
    a seed fetch permanently retired a campaign's best query.

    **A refusal is not an outage, and it is re-raised.** A 401, a 402 and an exhausted
    429 backoff are each a final answer that every subsequent call would repeat, so
    swallowing one turns *your key was rejected* into ``0`` new leads — the one failure
    ``core/errors.py`` exists to prevent. The job above catches it and stops with that
    error's own name. Only ``provider_unavailable`` — genuinely could not be reached —
    keeps its place on the frontier and returns ``None``.
    """
    from openoutreach.core.errors import ErrorType
    from openoutreach.core.pipeline.select import DISCOVERY_PAGE_SIZE
    from openoutreach.discovery import search, step_line
    from openoutreach.enrichment import bettercontact

    try:
        return search(node.to_filters(), limit=DISCOVERY_PAGE_SIZE, offset=offset)
    except bettercontact.BetterContactUnavailable as exc:
        if exc.error_type != ErrorType.PROVIDER_UNAVAILABLE:
            raise
        logger.warning("%s", step_line(
            "fetch", f"provider unavailable ({exc}) — leaving the node on the frontier",
            glyph="⚠", color="red"))
        return None


def _handle_empty(node, offset: int, page) -> str | None:
    """Decide what an empty page means, and retire the node if it means anything.

    Returns the verdict, or ``None`` when the page was a transport artifact and the node
    keeps its place on the frontier.
    """
    from openoutreach.core.pipeline import select
    from openoutreach.discovery import step_line

    # The count came back positive while the rows did not: that is the burst artifact of
    # §4, an answer about our call rather than about the query. Never retire on it.
    if page.leads_found:
        logger.warning("%s", step_line(
            "fetch", f"empty page but {page.leads_found:,} in the index — transport "
                     f"artifact, node kept", glyph="⚠", color="yellow"))
        return None

    verdict = select.retire(node, at_offset=offset)
    messages = {
        "dead": "nobody in the index matches this combination — node and its whole "
                "subtree pruned",
        "drained": f"vein exhausted at offset {offset} — every match is already a lead "
                   f"here, so the subtree is pruned too",
        "capped": f"hit the {select.REACH_CAP:,}-row reach cap — node retired, but its "
                  f"children open fresh windows",
    }
    # Nodes, subtrees, offsets and the reach cap are the walk's own vocabulary — the
    # retirement is real and worth reading, by whoever is reading the walk.
    logger.debug("%s", step_line("fetch", messages[verdict], glyph="✗", color="yellow"))
    return verdict


def discover(campaign, qualifier=None) -> int:
    """Fire frontier nodes until one returns leads. Returns the count of new Leads.

    ``0`` means the frontier is spanned (nothing unfired and nothing left to deepen) or a
    fetch was unavailable — both best-effort, since a provider outage must not fail the
    enclosing task. A provider *refusal* is the exception and propagates
    (``_fetch``): a rejected key must never be read as a query that matches nobody.
    ``qualifier`` is accepted and ignored: the GP no longer selects
    queries (§13), and the parameter stays only so the call sites in ``pools`` read the
    same for one release.

    Two gates: a campaign with no finder key cannot reach the index, and one with no
    product or target has nothing to build a query from. (A third stood here — the
    freemium promo campaign never searched, because it fed on leads other campaigns
    had already found. It is gone with that campaign.)
    """
    from openoutreach.core.pipeline import select
    from openoutreach.discovery import step_line
    from openoutreach.enrichment import bettercontact

    if not bettercontact.is_configured():
        return 0
    if not (campaign.product_docs or campaign.campaign_target):
        return 0

    logger.info(colored(f"▶ discover · {campaign}", "blue", attrs=["bold"]))

    store = select.LabelStore.load(campaign)
    keywords = _ensure_frontier(campaign, store)

    retired = 0
    while True:
        node = select.next_node(campaign, store)
        if node is None:
            logger.info(colored(
                f"■ no more people left to find for {campaign} — every search this "
                f"campaign knows how to make is exhausted", "blue"))
            logger.debug("frontier spanned · %d node(s) retired this pass", retired)
            return 0

        offset = node.next_offset
        page = _fetch(node, offset)
        if page is None:
            return 0  # outage: the node keeps its place, the caller carries on

        if not page.leads:
            if _handle_empty(node, offset, page) is None:
                return 0  # transport artifact — re-firing now would just repeat it
            retired += 1
            continue

        created = _harvest(campaign, node, page.leads)
        select.advance(node, leads_found=page.leads_found)
        grown = select.expand(node, store, keywords)
        logger.info("%s", step_line(
            "fetch", f"{created} new lead(s) from {len(page.leads)} row(s)",
            glyph="✓", color="green"))
        logger.debug("%s", step_line(
            "frontier", f"+{grown} node(s) from this page", glyph="+", color="cyan"))
        return created
