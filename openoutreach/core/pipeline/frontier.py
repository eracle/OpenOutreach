# openoutreach/core/pipeline/frontier.py
"""Discovery frontier — a lazy best-first walk over a campaign's query nodes.

Replaces the single ``(Campaign.icp_filters, discovery_offset)`` cursor. There is
no persisted queue of candidates: the next query is computed **lazily** from the
set of already-fetched ``DiscoveryQuery`` nodes, like the ``pools.py`` generator
chain. One discovery *move* (``discover.py``):

    rerank(current GP) → next_query → fetch → persist_fetched
                              │                    └ empty page → mark_exhausted
                              └ cold start → generate_seed (ICP)

``next_query`` picks exactly one query to fetch, driven only by real signals:

- **Bootstrap** *(qualifier pre-exploit)* — scores aren't trustworthy yet (a null
  score is *unknown*, not a wall), so page the seed linearly, exactly like the old
  cursor, feeding qualification the labels that train the GP.
- **Deepen** *(exploit, best node scores > 0)* — deepen the highest-scoring
  non-exhausted node to mine a productive vein.
- **Wall** *(exploit, every active node scores 0)* — ask the LLM for one new query.

The regime boundary is the qualifier's own ``n_neg > n_pos`` trust gate. Exhaustion
is reactive: a fetch that returns an empty page marks that ``params`` exhausted so
it is never re-picked. No explore-share cadence, no width target, no size cap — the
walk widens exactly when the current regions stop paying.

See the roadmap card ``p2-e3-discovery-query-graph-search``.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections import namedtuple

from django.db.models import Max

from openoutreach.core.models import DiscoveryQuery
from openoutreach.core.pipeline.ready_pool import count_accepted

logger = logging.getLogger(__name__)

DISCOVERY_PAGE_SIZE = 100

# The query to fetch next, plus which move produced it — ``bootstrap``, ``deepen``,
# or ``wall``. ``discover`` uses ``move`` for logging and to cap a move at a single
# wall fetch (a freshly opened region that comes back empty ends the move).
NextQuery = namedtuple("NextQuery", ["params", "offset", "move"])


# ── params identity ──────────────────────────────────────────────────

def canonicalize(params: dict) -> str:
    """Deterministic JSON for a filter dict — key-sorted, compact."""
    return json.dumps(params, sort_keys=True, separators=(",", ":"))


def params_hash(params: dict) -> str:
    """sha256 of the canonicalized params — the node-identity key for dedup."""
    return hashlib.sha256(canonicalize(params).encode()).hexdigest()


# ── seed ─────────────────────────────────────────────────────────────

def generate_seed(campaign) -> dict:
    """LLM-generate the campaign's ICP seed filters and fold its country onto it.

    Called only on a cold start (the campaign owns no nodes yet): the seed isn't
    cached — its first fetched page becomes the node that carries its params from
    then on. Folds ``country_code`` (used to geo-stamp every discovered Lead) onto
    the campaign. Returns the filter dict, or ``{}`` when the ICP is empty.
    """
    from openoutreach.core.pipeline.icp import generate_icp_spec

    spec = generate_icp_spec(campaign)
    filters = spec.get("filters") or {}
    if not filters:
        return {}

    country_code = spec.get("country_code", "")
    if country_code and campaign.country_code != country_code:
        campaign.country_code = country_code
        campaign.save(update_fields=["country_code"])
    logger.info("[%s] discovery seed: %s", campaign, filters)
    return filters


# ── persist / exhaust ────────────────────────────────────────────────

def persist_fetched(campaign, params: dict, offset: int) -> DiscoveryQuery:
    """Record a just-fetched ``(params, offset)`` page, deduped on the unique triple.

    Returns the node (created or pre-existing) so its first-touch leads can point
    back via ``Lead.discovered_by``. ``score`` stays null — the node is scored by
    the next move's re-rank, once qualification has retrained the GP on its leads.
    """
    node, _ = DiscoveryQuery.objects.get_or_create(
        campaign=campaign, params_hash=params_hash(params), offset=offset,
        defaults={"params": params},
    )
    return node


def mark_exhausted(campaign, params: dict) -> None:
    """Flag every node of a ``params`` line exhausted — its deepen hit an empty page.

    A whole query line is exhausted at once (all offsets share the fate of the
    deepest, dry page), so it drops out of ``rerank`` and ``next_query`` and is
    never re-picked.
    """
    DiscoveryQuery.objects.filter(
        campaign=campaign, params_hash=params_hash(params),
    ).update(exhausted=True)


# ── scoring ──────────────────────────────────────────────────────────

def _in_exploit_mode(qualifier) -> bool:
    """The qualifier's own explore→exploit switch (negatives outnumber positives)."""
    n_neg, n_pos = qualifier.class_counts
    return n_neg > n_pos


def rerank(campaign, qualifier) -> None:
    """Re-score every non-exhausted node against the current GP (no re-fetch).

    The GP retrained since the last move, so prior scores are stale. Scores come
    from each node's first-touch leads' embeddings (already persisted on ``Lead``),
    counting how many the GP would accept. No-op until the qualifier is in exploit
    mode — before that scores stay null and the walk pages the seed (bootstrap).

    This sweep is deliberately **uncapped**: a dry line is exhausted whole (all its
    offsets at once) and so leaves the sweep, discovery only runs when the pool
    needs leads, and the work per node is a vectorized ``predict_probs`` over
    embeddings already in the DB. At realistic campaign scale that keeps the active
    set small. A size cap was considered and rejected — it would be a magic constant
    bounding a cost we have not observed. If a long campaign ever makes this sweep
    hurt, cap it then, on evidence.
    """
    if not _in_exploit_mode(qualifier):
        return
    nodes = list(DiscoveryQuery.objects.filter(campaign=campaign, exhausted=False))
    for node in nodes:
        node.score = count_accepted(qualifier, node.lead_embeddings)
    if nodes:
        DiscoveryQuery.objects.bulk_update(nodes, ["score"])


# ── selection ────────────────────────────────────────────────────────

def _next_offset(campaign, hash_: str) -> int:
    """The next unfetched offset for a params line — max fetched offset + one page,
    or 0 if the line has never been fetched."""
    deepest = (
        DiscoveryQuery.objects
        .filter(campaign=campaign, params_hash=hash_)
        .aggregate(m=Max("offset"))["m"]
    )
    return 0 if deepest is None else deepest + DISCOVERY_PAGE_SIZE


def _bootstrap_query(campaign) -> NextQuery | None:
    """The next seed page to fetch pre-exploit — the old linear cursor.

    Pre-exploit the only line is the seed, so the earliest node *is* the seed's
    deepest-known page. Deepen it; on a cold start (no node yet) generate the seed
    from the ICP and start at offset 0.
    """
    seed = DiscoveryQuery.objects.filter(campaign=campaign).order_by("pk").first()
    if seed is None:
        filters = generate_seed(campaign)
        return NextQuery(filters, 0, "bootstrap") if filters else None
    if seed.exhausted:
        return None  # the seed line dried up before the GP could score elsewhere
    return NextQuery(seed.params, _next_offset(campaign, seed.params_hash), "bootstrap")


def next_query(campaign, qualifier) -> NextQuery | None:
    """The single query to fetch next, or None when nothing is left to walk.

    Assumes ``rerank`` already ran this move. Three moves:

    - **Bootstrap** (pre-exploit): page the seed linearly. A null score is unknown,
      not a wall, so we stay on our best prior until the GP can score. On a cold
      start (no nodes yet) the seed is generated from the ICP; thereafter the seed
      node itself carries its params. None if the ICP is empty or the seed line has
      dried up (no trustworthy scores to move elsewhere).
    - **Deepen** (exploit, best non-exhausted score > 0): deepen that node's line.
    - **Wall** (exploit, all scores 0): ask the LLM for one new query at offset 0.
      None if the LLM is dry or re-proposes an already-tried query.
    """
    if not _in_exploit_mode(qualifier):
        return _bootstrap_query(campaign)

    active = list(DiscoveryQuery.objects.filter(campaign=campaign, exhausted=False))
    best = max(
        (n for n in active if (n.score or 0) > 0),
        key=lambda n: (n.score, n.pk), default=None,
    )
    if best is not None:
        return NextQuery(best.params, _next_offset(campaign, best.params_hash), "deepen")

    # Wall — every active region is barren. Open one new region.
    from openoutreach.core.pipeline.mutate import generate_mutation

    params = generate_mutation(campaign)
    if not params:
        return None
    if DiscoveryQuery.objects.filter(
        campaign=campaign, params_hash=params_hash(params),
    ).exists():
        return None  # LLM re-proposed a query we already tried — nothing new to open
    return NextQuery(params, 0, "wall")
