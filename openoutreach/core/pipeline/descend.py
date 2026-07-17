# openoutreach/core/pipeline/descend.py
"""The lattice visit — compose a query, never invent one.

The LLM supplies **elements** and the descent composes **conjunctions**:
``icp.generate_seed`` gets 5 job titles from one call, the seed can carry exactly
one, and the other 4 used to be dropped on the floor and re-invented at the next
wall. They are the pool (``Campaign.clauses``) now, and the walk spans it.

**One iterator, one order, no probes.** Every node is a real fetch at
``DISCOVERY_PAGE_SIZE`` whose leads are kept — there is no cheap ``limit=1``
liveness call, because a query execution already answers the only question a probe
asked (does this match anybody?) and pays for the rows besides. What a node is
worth is counted from its leads' deals, not guessed at before fetching it.

The visit order::

    level N  →  level N-1  →  …  →  level 2  →  level 1  →  LLM refill

**Deepest first.** A long conjunction matches fewer people and so reaches past the
provider's famous-company head (``{lead_seniority: founder}`` → Meta, Meta, Meta)
straight into the niche where the ICP actually lives. The seed conjunction is the
head of level N, so the walk opens on the ICP's own strongest guess and only widens
as depth runs out — shorter levels come later because they widen back toward that
head, and singletons come last of all.

**Pruning still runs, but pays late.** The subset test is unchanged and still
correct, yet it does little *within* a descent now: the shortest, highest-pruning
empty sets (the singletons) are recorded last, so a clause the index carries nowhere
is not convicted until its singleton is finally reached, and until then it sits in
every deep conjunction holding it — each fetched, each empty. That extra emptiness
near the top is the accepted cost of reaching the niche first (chosen 2026-07-17).
The subset table earns its keep mostly *across refills*: a singleton empty already
on record then prunes freshly-composed supersets before they are ever fetched.

**Emptiness prunes; yield never does.** A barren *yield* verdict — those people
exist, they are not our ICP — writes nothing and retires nothing. Only a fetch that
matched nobody records an ``EmptyClauseSet``, and it convicts the whole set, never
a clause inside it: ``lead_department: Sales`` returns rows alone yet sat in six
0-row conjunctions. A node whose window is all-Meta can have a refinement whose
window is gold, so nothing about a bad view propagates.

This module only *reads* the pruning table. ``discover.py`` writes it, because it
is the leg that fetches and therefore the leg that learns.

See the roadmap card ``p2-e3-discovery-query-graph-search``.
"""
from __future__ import annotations

import itertools
import logging

from termcolor import colored

from openoutreach.core.models import EmptyClauseSet
from openoutreach.core.pipeline.frontier import clause_key
from openoutreach.discovery import describe_clauses

logger = logging.getLogger(__name__)


# ── the pool ─────────────────────────────────────────────────────────

def _pool(campaign) -> dict[str, list[str]]:
    """The campaign's clause pool, grouped by family, sorted by age.

    Nothing is excluded here. A clause the index carries nowhere is retired by the
    subset test instead — its singleton lands in ``EmptyClauseSet`` when the level-1
    pass finally reaches it (last, under deepest-first) and prunes every remaining
    candidate holding it — so there is no second liveness mechanism to keep in step
    with this one.

    Insertion order is the ICP's own ranking, and the seed took each family's first
    value, so it survives as the tie-break the visit orders by.
    """
    pool: dict[str, list[str]] = {}
    for family, value in campaign.clauses.order_by("pk").values_list("family", "value"):
        pool.setdefault(family, []).append(value)
    return pool


# ── pruning ──────────────────────────────────────────────────────────

def _empty_sets() -> list[frozenset]:
    """Every recorded empty conjunction, as clause-pair sets, for the subset test."""
    return [
        frozenset(s.clause_pairs)
        for s in EmptyClauseSet.objects.prefetch_related("clauses")
    ]


def _is_pruned(candidate: frozenset, empty_sets) -> bool:
    """Is this candidate a superset of a conjunction already known to be empty?

    The anti-monotone rule: adding clauses can only ever remove rows, so a candidate
    containing an empty set is empty too and never needs a fetch of its own. Under a
    deepest-first walk this bites mostly across refills, once a short empty set is on
    record to prune later supersets.
    """
    return any(empty <= candidate for empty in empty_sets)


# ── the visit ────────────────────────────────────────────────────────

def _visit_order(pool: dict[str, list[str]]) -> list[list[tuple[str, str]]]:
    """Every conjunction the pool spans, in visit order: N, N-1, …, 2, 1.

    Each family contributes one value or none, so a candidate is any non-empty
    sub-conjunction with at most one value per family — an OR is unrepresentable by
    construction (that is the point of composing from clauses rather than asking the
    LLM for a filter dict).

    Within a level, candidates sort by how far they sit from the pool's head — the
    value the seed took in each family — because the ICP's own ranking is the only
    prior the walk has before anything is fetched. Level N leads, so that makes the
    seed conjunction the first node visited, with no special case for it anywhere.
    """
    families = sorted(pool)
    ranks = {family: {v: i for i, v in enumerate(pool[family])} for family in families}
    choices = [[(family, v) for v in pool[family]] + [None] for family in families]

    candidates = [
        sorted(c for c in combo if c is not None)
        for combo in itertools.product(*choices)
    ]
    candidates = [c for c in candidates if c]
    deepest = max(len(c) for c in candidates)

    def rank(candidate):
        # Deepest first: depth N leads and depth 1 (singletons) comes last, so the
        # walk opens in the niche and only widens toward the head as depth runs out.
        level = deepest - len(candidate)
        distance = sum(ranks[family][value] for family, value in candidate)
        return level, distance, candidate

    candidates.sort(key=rank)
    return candidates


def _tried_keys(campaign) -> set[str]:
    """Clause-set keys this campaign has already fetched a page of."""
    from openoutreach.core.models import DiscoveryQuery

    return set(
        DiscoveryQuery.objects
        .filter(campaign=campaign)
        .values_list("clause_key", flat=True)
    )


def descend(campaign) -> list[tuple[str, str]]:
    """The next unvisited, unpruned conjunction from the pool, or ``[]``.

    A lookup, not a call: walk the pool's conjunctions in visit order and hand the
    frontier the first one this campaign has not fetched and the subset test has not
    already convicted. Whether it holds anybody is answered by fetching it.

    ``[]`` means the visit is genuinely out of conjunctions — every one the pool
    spans is fetched or pruned. That is the *only* condition under which the LLM is
    asked for new clauses (``mutate.descend_or_refill``); it is not a failure.
    """
    pool = _pool(campaign)
    if not pool:
        return []

    tried = _tried_keys(campaign)
    empty_sets = _empty_sets()
    for candidate in _visit_order(pool):
        if clause_key(candidate) in tried or _is_pruned(frozenset(candidate), empty_sets):
            continue
        logger.info("[%s] %s: %s", campaign,
                    colored("visit", "yellow", attrs=["bold"]),
                    colored(describe_clauses(candidate), "cyan"))
        return candidate

    logger.info("[%s] %s: every conjunction the pool spans is fetched or pruned",
                campaign, colored("visit exhausted", "yellow", attrs=["bold"]))
    return []
