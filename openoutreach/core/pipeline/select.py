# openoutreach/core/pipeline/select.py
"""Query selection — one GP scores every fetchable candidate, argmax wins.

The whole discovery walk, from first principles. Clauses are the axes; the primary
queries are **maximals** — one value per family, the full Cartesian product of the
campaign's clause pool. Breadth comes from *more clause values* (``mint.py``), never
from dropping clauses at composition time. The one loosening is **lazy backoff**: a
conjunction that matches nobody enqueues its one-clause-removed generalizations
(``_generalizations``), so the walk descends toward the non-empty frontier instead of
grinding through a Cartesian product of dead leaves — and recording those sub-maximal
empties finally gives the anti-monotone prune teeth *within* a single pool. Every
candidate, maximal or generalization, is scored the same way (see below) and, when it
returns rows, harvested the same way. See ``p2-e3-discovery-empty-set-backoff``.

Every next move is one candidate, scored by one value function:

- a **fresh** maximal (offset 0) — explore a region,
- a **deepen** of a fetched, non-exhausted maximal (its next page) — exploit a vein.

Both are scored the same way: embed the maximal's *keywords* (``discovery.embed_query``)
and read the GP's balance-driven acquisition (``qualifier.acquisition_scores`` —
predicted P in exploit mode, BALD info-gain in explore mode). Argmax picks the fetch.

Exact-embedding every maximal is too costly once the pool is large, so ``_prefilter``
first ranks the *whole* pool by a cheap composed score — embed only the pool's few
dozen distinct clause phrases, then pool them per query — and keeps the top-K on the
live axis (``qualifier.acquisition_mode``), and only those K are exact-embedded. Mean
pooling tracks exploit's P well and is complete at a small K; explore's BALD is a
variance that doesn't decompose over clauses, so it gets a larger K (see ``PREFILTER_K``).
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
from openoutreach.discovery import embed_queries, embed_query

logger = logging.getLogger(__name__)

DISCOVERY_PAGE_SIZE = 100

# The campaign's fixed ICP size band — it rides every maximal unchanged and is never a
# backoff axis (dropping a bound queries off-ICP; the provider fills a half-open or
# inverted band with any-size companies rather than returning nothing). Mirrors
# ``discover._HEADCOUNT_FAMILIES``.
_HEADCOUNT_FAMILIES = ("company_headcount_min", "company_headcount_max")

# Exact-embedding every fetchable maximal is the cost — a large clause pool spans a
# huge Cartesian product, and each candidate is a model forward pass (~10 ms). So the
# selector prefilters the *whole* pool with a cheap composed score (embed only the
# pool's few dozen distinct clause phrases, then pool them per query — free), keeps
# the top-K on the live acquisition axis, and exact-embeds only those K.
#
# The two axes have very different prefilter accuracy, so each gets its own K:
#   exploit — a query's embedding is ~the mean of its clause embeddings, so composed
#     P(f>0.5) tracks the truth (Spearman ~0.9); the true top is recovered with recall
#     1.0 by K≈128. A small K is genuinely complete here.
#   explore — BALD rewards posterior *variance*, a quadratic form that does not
#     decompose over clauses, so the cheap proxy (mean per-clause variance) is much
#     weaker (recall ~0.44 at K=1024). It gets a larger budget; K=1024 is the knee of
#     the recall/cost curve (~10 s) before deep diminishing returns. The proxy means
#     rather than sums so a mixed-depth pool (backoff admits sub-maximals) compares
#     candidates depth-neutrally, not by clause count.
# See the roadmap card ``p2-e3-discovery-unified-gp-query-selection``.
PREFILTER_K = {"exploit (p)": 256, "explore (BALD)": 1024}

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
    """Blacklist a conjunction the index matched nobody with. Idempotent, global.

    Only an offset-0 empty page belongs here — a deeper empty page is a vein running
    out, not a conjunction that matches nobody. The set can be any depth: a fired
    maximal, a backed-off generalization, or a size-1 pre-screen probe. Read back as the
    anti-monotone prune — a candidate is dead iff some recorded set is a subset of it —
    so recording a *sub*-maximal empty (e.g. the size-1 ``{location=Oman}`` a pre-screen
    writes) prunes every maximal that contains it in one shot, which is the leverage the
    backoff walks toward. See ``p2-e3-discovery-empty-set-backoff``.
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
    """Order a conjunction by *mean* distance from the pool's head, so the seed leads.

    The mean, not the sum: once backoff admits sub-maximal candidates the pool is
    mixed-depth, and a summed rank would score a shorter conjunction closer to the
    head purely for holding fewer clauses. Averaging keeps the cold-start order
    depth-neutral — the same reason the explore prefilter proxy means its per-clause
    variances rather than summing them.
    """
    rank = {f: {v: i for i, v in enumerate(vs)} for f, vs in pool.items()}
    return lambda conjunction: (
        sum(rank[f][v] for f, v in conjunction) / len(conjunction) if conjunction else 0.0
    )


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


def _generalizations(empty_sets: list[frozenset]) -> list[list[tuple[str, str]]]:
    """One-clause-removed children of every recorded empty conjunction — the lazy backoff.

    Emptiness is monotone: a conjunction that matched nobody says nothing new about its
    supersets (already empty) but licenses trying its immediate *sub*-conjunctions —
    each drops a single clause and may well match someone. A child that itself fetches
    empty is recorded in turn, so its own children surface next pass: the descent walks
    one level at a time toward the non-empty frontier, generating only the children of
    empties actually hit, never the whole lattice.

    Only *value* clauses are dropped; the headcount band rides every child unchanged
    (``_HEADCOUNT_FAMILIES``) — loosening a bound queries off-ICP. A set with one value
    clause (or none) contributes no child: its only value-less descent is the bare band,
    which matches everyone and is not a candidate. This is what makes a size-1 pre-screen
    empty inert here, and — since a legacy band-bundled pre-screen empty has exactly one
    value clause too — keeps it from resurrecting a pruned value. See the roadmap card
    ``p2-e3-discovery-empty-set-backoff``.
    """
    children = []
    for empty in empty_sets:
        droppable = [c for c in empty if c[0] not in _HEADCOUNT_FAMILIES]
        if len(droppable) <= 1:
            continue
        for clause in droppable:
            children.append(sorted(empty - {clause}))
    return children


def _candidates(campaign, pool: dict[str, list[str]]) -> list[NextQuery]:
    """Every fetchable candidate as a ``NextQuery``, minus exhausted and empty-pruned.

    The candidate frontier is the pool's maximals **and** the one-clause-removed
    generalizations of every recorded empty (``_generalizations`` — the backoff),
    deduped by ``clause_key`` so a child shared by several empties is offered once. A
    fetched, non-exhausted set yields its next page (deepen); an untried one yields
    offset 0 (fresh). A set that is recorded empty, or a superset of a recorded-empty
    set, is dropped. The frontier is re-derived every call rather than persisted: the GP
    re-scores between calls so a stored queue would only be re-ranked anyway, and
    ``EmptyClauseSet`` already holds the recursion state the backoff descends.
    """
    lines = _line_state(campaign)
    empty_keys = set(EmptyClauseSet.objects.values_list("clause_key", flat=True))
    empty_sets = _empty_sets()

    # Dedup maximals against backoff children by clause_key — many maximals share the
    # same n−1 child, and a child can be reached from several empties.
    frontier: dict[str, list[tuple[str, str]]] = {}
    for conjunction in itertools.chain(_maximals(pool), _generalizations(empty_sets)):
        frontier.setdefault(clause_key(conjunction), conjunction)

    candidates = []
    for key, conjunction in frontier.items():
        line = lines.get(key)
        if line and line["exhausted"]:
            continue
        if key in empty_keys or any(empty <= frozenset(conjunction) for empty in empty_sets):
            continue
        offset = line["max_offset"] + DISCOVERY_PAGE_SIZE if line else 0
        candidates.append(NextQuery(conjunction, offset))
    return candidates


# ── selection ────────────────────────────────────────────────────────

def _prefilter(candidates: list[NextQuery], qualifier, strategy: str) -> list[NextQuery]:
    """The top-K maximals to exact-embed, ranked by a cheap composed score.

    Embeds only the pool's distinct clause phrases (dozens), never the Cartesian
    product (thousands), then scores every candidate on the live acquisition axis:

    - exploit — composed query embedding (mean of its clause embeddings) → P(f>0.5),
    - explore — summed per-clause posterior variance, a cheap BALD proxy.

    Returns the whole list unchanged when it already fits within K.
    """
    phrases = sorted({pair for q in candidates for pair in q.clauses})
    idx = {pair: i for i, pair in enumerate(phrases)}
    phrase_emb = embed_queries([[pair] for pair in phrases]).astype(np.float64)

    if strategy == "exploit (p)":
        composed = np.array([phrase_emb[[idx[p] for p in q.clauses]].mean(axis=0)
                             for q in candidates])
        scores = qualifier.predict_probs(composed)
    else:
        variance = qualifier.posterior_std(phrase_emb) ** 2
        scores = np.array([variance[[idx[p] for p in q.clauses]].mean()
                           for q in candidates])

    K = PREFILTER_K[strategy]
    if len(candidates) <= K:
        return candidates
    keep = np.argsort(-np.asarray(scores, dtype=np.float64))[:K]
    return [candidates[i] for i in keep]


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
    # choice when the GP has no signal.
    ranker = _ranker(pool)
    candidates.sort(key=lambda q: (q.offset, ranker(q.clauses)))

    # The live acquisition axis, known before any exact-embed. None → cold start.
    strategy = qualifier.acquisition_mode()
    if strategy is None:
        return candidates[0]  # cold start — seed-first, fresh-first

    # Prefilter the whole pool cheaply, then exact-embed and score only the top-K.
    subset = _prefilter(candidates, qualifier, strategy)
    if len(candidates) > len(subset):
        logger.debug("[%s] %s: exact-scoring %d of %d maximals (prefiltered)",
                     campaign, strategy, len(subset), len(candidates))

    embeddings = embed_queries([q.clauses for q in subset]).astype(np.float64)
    _, scores = qualifier.acquisition_scores(embeddings)
    best = subset[int(np.argmax(scores))]
    logger.debug("[%s] query %s: %s", campaign, strategy, best.clauses)
    return best
