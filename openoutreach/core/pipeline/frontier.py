# openoutreach/core/pipeline/frontier.py
"""Discovery frontier — best-first search over a campaign's DiscoveryQuery nodes.

Replaces the single ``(Campaign.icp_filters, discovery_offset)`` cursor with a
persisted frontier of query nodes. One discovery *move* (``discover.py``):

    ensure_seed → rerank(fetched, current GP) → pick(next PENDING) → fetch →
    mark_fetched(+score) → expand(deepen + broad mutations) → enforce_size_cap

PENDING nodes are the fringe (fetchable, no leads yet); FETCHED nodes are the
explored interior (scored, expandable). A node is fetched exactly once, so
visited nodes are never revisited. Re-rank re-scores the FETCHED interior each
move (the GP retrained since last move), and PENDING children inherit their
pick-priority from their FETCHED parent's score.

See the roadmap card ``p2-e3-discovery-query-graph-search``. The size cap,
frontier width, and explore share are the build-time tuning knobs it flagged.
"""
from __future__ import annotations

import hashlib
import json
import logging

from django.db.models import F

from openoutreach.core.models import DiscoveryQuery
from openoutreach.core.pipeline.ready_pool import count_accepted

logger = logging.getLogger(__name__)

DISCOVERY_PAGE_SIZE = 100
# Hard resource ceiling on active nodes per campaign — not a policy knob: past it,
# the lowest-value FETCHED nodes are retired so the per-move re-rank (a sweep over
# fetched nodes) stays bounded regardless of how long a campaign runs.
FRONTIER_SIZE_CAP = 200
# The explore share: in exploit mode, one pick in EXPLORE_EVERY is reserved for a
# fresh unexplored region so a hot branch can't starve the graph. This is the
# card's single sanctioned heuristic knob — the principled replacement (a Bayesian
# acquisition function over the GP posterior) is deferred to Future work.
EXPLORE_EVERY = 5


# ── params identity ──────────────────────────────────────────────────

def canonicalize(params: dict) -> str:
    """Deterministic JSON for a filter dict — key-sorted, compact."""
    return json.dumps(params, sort_keys=True, separators=(",", ":"))


def params_hash(params: dict) -> str:
    """sha256 of the canonicalized params — the node-identity key for dedup."""
    return hashlib.sha256(canonicalize(params).encode()).hexdigest()


# ── enqueue / seed ───────────────────────────────────────────────────

def enqueue(campaign, params: dict, offset: int = 0, parent=None) -> DiscoveryQuery | None:
    """Create a PENDING node, deduped on ``(campaign, params_hash, offset)``.

    Returns the new node, or None when an equivalent node already exists in any
    status — a fetched or retired twin means we have already been there.
    """
    h = params_hash(params)
    if DiscoveryQuery.objects.filter(campaign=campaign, params_hash=h, offset=offset).exists():
        return None
    return DiscoveryQuery.objects.create(
        campaign=campaign, params=params, params_hash=h, offset=offset,
        parent=parent, status=DiscoveryQuery.Status.PENDING,
    )


def ensure_seed(campaign) -> None:
    """Seed the frontier from the campaign's ICP if it owns no nodes yet.

    LLM-generates the ICP filter spec once, folds its ``country_code`` onto the
    campaign, and enqueues a single PENDING seed node at offset 0. Idempotent —
    a campaign that already owns any node (or whose spec is empty) is untouched.
    """
    if DiscoveryQuery.objects.filter(campaign=campaign).exists():
        return

    from openoutreach.core.pipeline.icp import generate_icp_spec

    spec = generate_icp_spec(campaign)
    filters = spec.get("filters") or {}
    if not filters:
        return

    country_code = spec.get("country_code", "")
    if country_code and campaign.country_code != country_code:
        campaign.country_code = country_code
        campaign.save(update_fields=["country_code"])

    enqueue(campaign, filters, offset=0, parent=None)
    logger.info("[%s] frontier seeded: %s", campaign, filters)


# ── scoring ──────────────────────────────────────────────────────────

def _in_exploit_mode(qualifier) -> bool:
    """The qualifier's own explore→exploit switch (negatives outnumber positives)."""
    n_neg, n_pos = qualifier.class_counts
    return n_neg > n_pos


def mark_fetched(node: DiscoveryQuery, qualifier) -> None:
    """Flip a just-fetched node to FETCHED and score it from its new leads.

    Score = ``count_accepted`` over the node's first-touch leads' embeddings, set
    only in exploit mode; before that the P(f>0.5) ranking is noise, so score
    stays null. Computing it here lets ``expand`` judge whether to deepen.
    """
    node.status = DiscoveryQuery.Status.FETCHED
    node.score = count_accepted(qualifier, node.lead_embeddings) if _in_exploit_mode(qualifier) else None
    node.save(update_fields=["status", "score", "updated_at"])


def rerank(campaign, qualifier) -> None:
    """Re-score every FETCHED node against the current GP (no re-fetch).

    The GP retrained since the last move, so prior scores are stale. Scores come
    from each node's first-touch leads' embeddings (already persisted on ``Lead``),
    counting how many the GP would accept. No-op until the qualifier is in exploit
    mode — before that scores stay null and the frontier expands broad-unranked.
    """
    if not _in_exploit_mode(qualifier):
        return
    nodes = list(DiscoveryQuery.objects.filter(campaign=campaign, status=DiscoveryQuery.Status.FETCHED))
    for node in nodes:
        node.score = count_accepted(qualifier, node.lead_embeddings)
    if nodes:
        DiscoveryQuery.objects.bulk_update(nodes, ["score"])


# ── pick ─────────────────────────────────────────────────────────────

def pick(campaign, qualifier) -> DiscoveryQuery | None:
    """The next PENDING node to fetch, or None when the frontier is exhausted.

    Explore mode (qualifier pre-exploit): breadth-first and unranked — the oldest
    PENDING node, mutations (offset 0) before deepens, so the search fans wide
    while it gathers the balanced labels the exploit switch needs.

    Exploit mode: mostly the highest-value PENDING node, ranked by its FETCHED
    parent's score (a PENDING node has no leads of its own to score yet); one pick
    in EXPLORE_EVERY is reserved for a *fresh* unexplored region — the newest broad
    (offset-0) mutation — so a hot branch can't starve the graph.
    """
    pending = list(
        DiscoveryQuery.objects
        .filter(campaign=campaign, status=DiscoveryQuery.Status.PENDING)
        .select_related("parent")
    )
    if not pending:
        return None

    if not _in_exploit_mode(qualifier):
        # breadth-first: mutations (offset 0) before deepens, then FIFO
        return min(pending, key=lambda n: (n.offset, n.pk))

    n_fetched = DiscoveryQuery.objects.filter(
        campaign=campaign, status=DiscoveryQuery.Status.FETCHED,
    ).count()
    if n_fetched % EXPLORE_EVERY == 0:
        fresh = [n for n in pending if n.offset == 0]
        if fresh:
            return max(fresh, key=lambda n: n.pk)  # newest unexplored region

    def rank_key(node):
        score = node.parent.score if node.parent and node.parent.score is not None else -1.0
        return (-score, node.offset, node.pk)  # highest score, then breadth, then FIFO

    return min(pending, key=rank_key)


# ── expand ───────────────────────────────────────────────────────────

def expand(campaign, node: DiscoveryQuery, qualifier) -> None:
    """Grow the frontier by at most ONE node — breadth by default, depth the exception.

    The choice is driven by signals we already have — the qualifier's own
    explore→exploit state and the node's measured value — not a fixed width:

    - **Depth (exception)** fires only in exploit mode on a node that scored above
      zero: a productive vein worth mining another page (``offset + page``).
    - **Breadth (default)** asks the LLM for one new distinct query. Pre-exploit
      that's every move (the broad, unranked fan-out that also produces the balanced
      labels the exploit switch needs); in exploit it fires whenever the picked node
      wasn't a productive vein — a barren region spawns a fresh one to try instead.
    - **Depth as fallback**: if the LLM returns nothing pre-exploit, deepen to keep
      linear progress (the degenerate one-node case = the old single cursor).

    One node in (a fetch), one out (this enqueue), so the frontier is size-conserved.
    """
    def deepen():
        enqueue(campaign, node.params, offset=node.offset + DISCOVERY_PAGE_SIZE, parent=node)

    if _in_exploit_mode(qualifier) and (node.score or 0) > 0:
        deepen()  # mine a productive vein
        return

    from openoutreach.core.pipeline.mutate import generate_mutation

    params = generate_mutation(campaign)  # widen into a new region
    if params:
        enqueue(campaign, params, offset=0, parent=node)
    elif not _in_exploit_mode(qualifier):
        deepen()  # LLM dry pre-exploit → keep linear progress


# ── retire / size-cap ────────────────────────────────────────────────

def retire(node: DiscoveryQuery) -> None:
    """Drop a node off the frontier (dry page, or size-cap eviction)."""
    node.status = DiscoveryQuery.Status.RETIRED
    node.save(update_fields=["status", "updated_at"])


def enforce_size_cap(campaign) -> None:
    """Retire the lowest-value FETCHED nodes past FRONTIER_SIZE_CAP.

    Keeps the per-move re-rank bounded. Only FETCHED nodes are evicted — PENDING
    nodes are the unexplored fringe, and retiring them would drop regions we have
    not yet tried. Lowest score first (unscored/oldest break the tie).
    """
    active = DiscoveryQuery.objects.filter(
        campaign=campaign,
        status__in=[DiscoveryQuery.Status.PENDING, DiscoveryQuery.Status.FETCHED],
    ).count()
    overflow = active - FRONTIER_SIZE_CAP
    if overflow <= 0:
        return

    evictable = (
        DiscoveryQuery.objects
        .filter(campaign=campaign, status=DiscoveryQuery.Status.FETCHED)
        .order_by(F("score").asc(nulls_first=True), "created_at")[:overflow]
    )
    for node in list(evictable):
        retire(node)
