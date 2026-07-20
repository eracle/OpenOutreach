# openoutreach/core/pipeline/descend.py
"""The lattice visit: choose the next query to fetch, deepest first.

The LLM supplies clauses (one value per family); this module composes conjunctions
from them and makes no provider call. It returns the deepest unfetched conjunction
the pool spans, and widens to a shorter one only when a deeper query came back empty
— anti-monotonicity: an empty query's supersets are empty too, but its subsets may
still hold people. An ``EmptyClauseSet`` therefore does double duty: it prunes every
superset of an empty conjunction, and it unlocks that conjunction's drop-one children
(recursively). ``[]`` means the pool is fully spanned — the signal to refill from the
LLM (``mutate.descend_or_refill``).

See the roadmap card ``p2-e3-discovery-query-graph-search``.
"""
from __future__ import annotations

import itertools
import logging

from termcolor import colored

from openoutreach.core.models import DiscoveryQuery, EmptyClauseSet
from openoutreach.core.pipeline.frontier import clause_key
from openoutreach.discovery import describe_clauses

logger = logging.getLogger(__name__)


# ── the conjunctions the pool spans ──────────────────────────────────

def _pool(campaign) -> dict[str, list[str]]:
    """Clause values grouped by family, in insertion order (the ICP's ranking)."""
    pool: dict[str, list[str]] = {}
    for family, value in campaign.clauses.order_by("pk").values_list("family", "value"):
        pool.setdefault(family, []).append(value)
    return pool


def _maximal(pool: dict[str, list[str]]) -> list[list[tuple[str, str]]]:
    """The deepest conjunctions — one value from every family (their Cartesian product)."""
    families = sorted(pool)
    return [
        sorted(zip(families, combo))
        for combo in itertools.product(*(pool[f] for f in families))
    ]


def _children(conjunction: list[tuple[str, str]]):
    """Each drop-one-clause widening of a conjunction (empty for a singleton)."""
    for i in range(len(conjunction)):
        child = conjunction[:i] + conjunction[i + 1:]
        if child:
            yield child


def _ranker(pool: dict[str, list[str]]):
    """Order a conjunction by distance from the pool's head, so the seed leads."""
    rank = {f: {v: i for i, v in enumerate(vs)} for f, vs in pool.items()}
    return lambda conjunction: sum(rank[f][v] for f, v in conjunction)


# ── pruning ──────────────────────────────────────────────────────────

def _empty_sets() -> list[frozenset]:
    """Recorded empty conjunctions as clause-pair sets, for the subset test."""
    return [frozenset(s.clause_pairs) for s in EmptyClauseSet.objects.prefetch_related("clauses")]


def _is_pruned(candidate: frozenset, empty_sets: list[frozenset]) -> bool:
    """A candidate is dead iff it contains a conjunction already known to be empty."""
    return any(empty <= candidate for empty in empty_sets)


def _tried_keys(campaign) -> set[str]:
    """Clause-set keys this campaign has already fetched a page of."""
    return set(
        DiscoveryQuery.objects.filter(campaign=campaign).values_list("clause_key", flat=True)
    )


# ── the visit ────────────────────────────────────────────────────────

def _fetchable_conjunctions(pool, tried, empty_sets):
    """Yield fetchable conjunctions deepest first, widening only below empty ones.

    A conjunction that is empty (recorded or inferred by the subset test) is never
    fetched again but unlocks its children; a fetched *non-empty* one is a dead end —
    the visit never widens away from a query that works.
    """
    empty_keys = set(EmptyClauseSet.objects.values_list("clause_key", flat=True))
    rank = _ranker(pool)
    wave = sorted(_maximal(pool), key=rank)
    while wave:
        widenings: dict[str, list[tuple[str, str]]] = {}
        for conjunction in wave:
            key = clause_key(conjunction)
            if key in empty_keys or _is_pruned(frozenset(conjunction), empty_sets):
                for child in _children(conjunction):
                    widenings.setdefault(clause_key(child), child)
            elif key not in tried:
                yield conjunction
        wave = sorted(widenings.values(), key=rank)


def descend(campaign) -> list[tuple[str, str]]:
    """The deepest unvisited, unpruned conjunction, or ``[]`` when the pool is spanned."""
    pool = _pool(campaign)
    if not pool:
        return []
    for conjunction in _fetchable_conjunctions(pool, _tried_keys(campaign), _empty_sets()):
        logger.info("[%s] %s: %s", campaign,
                    colored("visit", "yellow", attrs=["bold"]),
                    colored(describe_clauses(conjunction), "cyan"))
        return conjunction
    logger.info("[%s] %s: every conjunction the pool spans is fetched or pruned",
                campaign, colored("visit exhausted", "yellow", attrs=["bold"]))
    return []
