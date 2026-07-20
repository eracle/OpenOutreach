# openoutreach/core/pipeline/select.py
"""Query selection — one GP scores every fetchable maximal, argmax wins.

The whole discovery walk, from first principles. Clauses are the axes; the only
queries ever fired are **maximals** — one value per family, the full Cartesian
product of the campaign's clause pool. Breadth comes from *more clause values*
(``mint.py``), never from dropping clauses, so nothing looser than a full ICP point
is ever fetched. That is precision by construction — the loose queries that pulled
the provider's famous-company head are simply never in the candidate set.

Every next move is one candidate, scored by one value function:

- a **fresh** maximal (offset 0) — explore a region,
- a **deepen** of a fetched, non-exhausted maximal (its next page) — exploit a vein.

Both are scored the same way: embed the maximal's *keywords* (``discovery.embed_query``)
and read the GP's balance-driven acquisition (``qualifier.acquisition_scores`` —
predicted P in exploit mode, BALD info-gain in explore mode). Argmax picks the fetch.
There is no deepen/visit alternation and no counted-deal metric: the GP that ranks
which lead to label also ranks which query to fetch, because a discovered lead carries
its retrieving query's keywords in its embedding (``db/leads.create_lead``), so the GP
learns query-term → fit from ordinary labelling. Deepen-vs-explore is not two policies;
it is argmax over one score, and a vein bounds itself — a maximal empties within the
provider's 10k window and is marked ``exhausted``, dropping out of the candidates.

Cold start (GP unfitted): acquisition returns ``None`` and selection falls back to
seed-first, fresh-before-deep order — correct behaviour when the model has no signal,
and strictly simpler than the walk it replaces.

``next_query`` returning ``None`` means the pool spans nothing fetchable — the
saturation signal ``discover`` answers by minting clauses. See the roadmap card
``p2-e3-discovery-unified-gp-query-selection``.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import logging
from collections import namedtuple

import numpy as np

from openoutreach.core.models import Clause, DiscoveryQuery, EmptyClauseSet
from openoutreach.discovery import embed_query

logger = logging.getLogger(__name__)

DISCOVERY_PAGE_SIZE = 100

# Most maximals scored (hence embedded) per move. A large clause pool spans a huge
# Cartesian product; scoring is free but embedding each candidate is not, so we cap
# to the seed-closest slice. Not a coverage cut — as those exhaust, deeper-ranked
# maximals enter the window on later moves.
MAX_CANDIDATES = 64

# The query to fetch next: a clause set and the offset to page it at. Nothing is
# persisted until the fetch returns rows. offset 0 is a fresh maximal; offset > 0 is
# a deepen of a vein already fetched.
NextQuery = namedtuple("NextQuery", ["clauses", "offset"])


# ── clause-set identity ──────────────────────────────────────────────

def canonicalize(clauses) -> str:
    """Deterministic text for a clause set — sorted ``family=value`` pairs."""
    return json.dumps(sorted(clauses), separators=(",", ":"))


def clause_key(clauses) -> str:
    """sha256 of the canonicalized clause set — the node-identity key for dedup."""
    return hashlib.sha256(canonicalize(clauses).encode()).hexdigest()


# ── persist / exhaust / blacklist ────────────────────────────────────

def persist_fetched(campaign, clauses, offset: int) -> DiscoveryQuery:
    """Record a just-fetched ``(clause set, offset)`` page, deduped on the triple.

    Returns the node so its first-touch leads can point back via
    ``Lead.discovered_by`` — the link that carries the query's keywords into each
    lead's embedding.
    """
    node, created = DiscoveryQuery.objects.get_or_create(
        campaign=campaign, clause_key=clause_key(clauses), offset=offset,
    )
    if created:
        node.clauses.set(Clause.rows_for(clauses))
    return node


def mark_exhausted(campaign, clauses) -> None:
    """Flag every page of a maximal exhausted — its fetch hit an empty page.

    The whole line shares the fate of its deepest, dry page, so it drops out of the
    candidate set. Emptiness is the only thing that retires a line; a barren *yield*
    (leads that exist but don't qualify) is a verdict about a view, not the query.
    """
    DiscoveryQuery.objects.filter(
        campaign=campaign, clause_key=clause_key(clauses),
    ).update(exhausted=True)


def record_empty(clauses) -> None:
    """Blacklist a maximal the index matched nobody with. Idempotent, global.

    Only an offset-0 empty page belongs here — a deeper empty page is a vein running
    out, not a conjunction that matches nobody. Read back as the anti-monotone prune:
    a candidate is dead iff some recorded set is a subset of it, so a maximal recorded
    empty before a family was minted prunes every deeper maximal that now contains it,
    without another fetch.
    """
    entry, created = EmptyClauseSet.objects.get_or_create(clause_key=clause_key(clauses))
    if created:
        entry.clauses.set(Clause.rows_for(clauses))


# ── the maximals the pool spans ──────────────────────────────────────

def _pool(campaign) -> dict[str, list[str]]:
    """Clause values grouped by family, in insertion order (the ICP's ranking)."""
    pool: dict[str, list[str]] = {}
    for family, value in campaign.clauses.order_by("pk").values_list("family", "value"):
        pool.setdefault(family, []).append(value)
    return pool


def _maximals(pool: dict[str, list[str]]) -> list[list[tuple[str, str]]]:
    """One value from every family — the Cartesian product, the only queries fired."""
    families = sorted(pool)
    return [
        sorted(zip(families, combo))
        for combo in itertools.product(*(pool[f] for f in families))
    ]


def _ranker(pool: dict[str, list[str]]):
    """Order a conjunction by distance from the pool's head, so the seed leads."""
    rank = {f: {v: i for i, v in enumerate(vs)} for f, vs in pool.items()}
    return lambda conjunction: sum(rank[f][v] for f, v in conjunction)


def _line_state(campaign) -> dict[str, dict]:
    """Per fetched maximal (by clause_key): high-water offset and whether exhausted."""
    lines: dict[str, dict] = {}
    for key, offset, exhausted in (
        DiscoveryQuery.objects.filter(campaign=campaign)
        .values_list("clause_key", "offset", "exhausted")
    ):
        line = lines.setdefault(key, {"max_offset": offset, "exhausted": exhausted})
        line["max_offset"] = max(line["max_offset"], offset)
        line["exhausted"] = line["exhausted"] or exhausted
    return lines


def _empty_sets() -> list[frozenset]:
    """Recorded empty conjunctions as clause-pair sets, for the subset test."""
    return [frozenset(s.clause_pairs) for s in EmptyClauseSet.objects.prefetch_related("clauses")]


def _candidates(campaign, pool: dict[str, list[str]]) -> list[NextQuery]:
    """Every fetchable maximal as a ``NextQuery``, minus exhausted and empty-pruned.

    A fetched, non-exhausted maximal yields its next page (deepen); an untried one
    yields offset 0 (fresh). A maximal that is recorded empty, or a superset of a
    recorded-empty set, is dropped.
    """
    lines = _line_state(campaign)
    empty_keys = set(EmptyClauseSet.objects.values_list("clause_key", flat=True))
    empty_sets = _empty_sets()

    candidates = []
    for conjunction in _maximals(pool):
        key = clause_key(conjunction)
        line = lines.get(key)
        if line and line["exhausted"]:
            continue
        if key in empty_keys or any(empty <= frozenset(conjunction) for empty in empty_sets):
            continue
        offset = line["max_offset"] + DISCOVERY_PAGE_SIZE if line else 0
        candidates.append(NextQuery(conjunction, offset))
    return candidates


# ── selection ────────────────────────────────────────────────────────

def next_query(campaign, qualifier) -> NextQuery | None:
    """The single maximal to fetch next, chosen by the GP, or ``None`` if saturated.

    ``None`` means every maximal the pool spans is fetched, exhausted or empty — the
    signal for ``discover`` to mint fresh clauses and recompose the product.
    """
    pool = _pool(campaign)
    if not pool:
        return None

    candidates = _candidates(campaign, pool)
    if not candidates:
        return None

    # Seed-first, fresh-before-deep — the deterministic order, and the cold-start
    # choice when the GP has no signal. Cap the slice we embed to bound cost.
    ranker = _ranker(pool)
    candidates.sort(key=lambda q: (q.offset, ranker(q.clauses)))
    if len(candidates) > MAX_CANDIDATES:
        logger.debug("[%s] scoring %d of %d maximals (seed-closest)",
                     campaign, MAX_CANDIDATES, len(candidates))
        candidates = candidates[:MAX_CANDIDATES]

    embeddings = np.array([embed_query(q.clauses) for q in candidates], dtype=np.float64)
    scored = qualifier.acquisition_scores(embeddings)
    if scored is None:
        return candidates[0]  # cold start — seed-first, fresh-first

    strategy, scores = scored
    best = candidates[int(np.argmax(scores))]
    logger.debug("[%s] query %s: %s", campaign, strategy, best.clauses)
    return best
