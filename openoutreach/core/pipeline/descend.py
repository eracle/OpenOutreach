# openoutreach/core/pipeline/descend.py
"""The wall move as a lattice lookup — compose a query, never invent one.

When every region the walk can see stops paying, ``frontier.next_query`` walls and
asks for one new query. That used to cost an LLM call *every time* — 7 mutations in
a few minutes on the observed run, each a whole conjunction invented from a list,
6 of them returning 0 rows. The clauses were already there: ``icp.generate_seed``
gets 5 job titles from one call, the seed can carry exactly one, and the other 4
were dropped on the floor and re-invented at the next wall.

So the LLM supplies **elements** and the descent composes **conjunctions**:

    pool (Campaign.clauses)  →  singleton sweep  →  deepest untried survivor
                                     │
                                     └ empty → Clause.is_live=False → its whole
                                       slice of the lattice is gone, one call

**Probe first, fetch second — they are not the same job.** A probe is ``limit=1``
and creates no leads; a fetch is ``limit=100`` and creates up to 100, which cost
*examination*, the one genuinely scarce resource (5.9% of the corpus has ever had
any). That split is what makes the singleton sweep affordable: the shortest
conjunctions are exactly the ones whose window fills with the provider's
famous-company head (``{lead_seniority: founder}`` → Meta, Meta, Meta), so probing
them maps the lattice while *fetching* them would pump the head straight into the
pool — the opposite of a ``headcount 1–20`` ICP.

**Sweep singletons, then go deep.** Probing each pool clause alone is the
highest-value call in the lattice: emptiness is anti-monotone, so a clause the
index carries nowhere kills every conjunction containing it. ``lead_location:
Europe`` — a region, not a country, matching zero leads — dies on one probe and is
pruned from every candidate forever, instead of poisoning query after query. It is
also the only *sound* way to convict a clause: alone, a clause has nothing else to
blame. What gets fetched is then the **deepest** surviving conjunction, because a
long conjunction matches fewer people and so reaches past the head into the niche.

**Emptiness prunes; yield never does.** A wall is a *yield* verdict — those people
exist, they are not our ICP — so it writes nothing to the blacklist and retires no
clause. Only a probe that matched nobody records anything. A node whose window is
all-Meta can have a refinement whose window is gold, so nothing about a bad view
propagates.

See the roadmap card ``p2-e3-discovery-query-graph-search``.
"""
from __future__ import annotations

import itertools
import logging

from termcolor import colored

from openoutreach.core.models import Clause, DiscoveryQuery, EmptyClauseSet
from openoutreach.core.pipeline.frontier import clause_key
from openoutreach.discovery import describe_clauses, probe

logger = logging.getLogger(__name__)


# ── the pool ─────────────────────────────────────────────────────────

def _live_pool(campaign) -> dict[str, list[str]]:
    """The campaign's un-retired pool clauses, grouped by family, sorted by age.

    Excludes only clauses a singleton probe has proven dead (``is_live=False``).
    An *unprobed* clause is kept: it is unknown, not barren, and the sweep is what
    resolves it. Insertion order is the ICP's own ranking, and the seed took each
    family's first value, so it survives as the tie-break the descent orders by.
    """
    pool: dict[str, list[str]] = {}
    clauses = campaign.clauses.exclude(is_live=False).order_by("pk")
    for family, value in clauses.values_list("family", "value"):
        pool.setdefault(family, []).append(value)
    return pool


def _sweep_singletons(campaign) -> int:
    """Probe every never-probed pool clause on its own; retire the dead. Returns the count.

    One call per clause, and each one either confirms a clause or deletes an entire
    slice of the lattice — no other probe in the walk prunes that much. Bounded by
    the pool size and run once per clause for all time (the verdict is global: a
    clause is dead for every campaign, because emptiness is a fact about the
    provider's index).

    Serial by requirement, not preference — concurrent probes cause 300s poll
    timeouts and corrupt results — so this is ~45s per unprobed clause, paid at the
    first wall and essentially never again.
    """
    unprobed = list(campaign.clauses.filter(is_live=None).order_by("pk"))
    if not unprobed:
        return 0

    logger.info("[%s] %s: %d unprobed clause(s)", campaign,
                colored("clause sweep", "magenta", attrs=["bold"]), len(unprobed))
    for clause in unprobed:
        clause.is_live = probe([(clause.family, clause.value)])
        clause.save(update_fields=["is_live"])
        logger.info("[%s]   %s %s", campaign,
                    colored("live " if clause.is_live else "dead ", "green" if clause.is_live else "red"),
                    colored(str(clause), "cyan"))
    return len(unprobed)


# ── pruning ──────────────────────────────────────────────────────────

def _empty_sets() -> list[frozenset]:
    """Every recorded empty conjunction, as clause-pair sets, for the subset test."""
    return [
        frozenset(s.clause_pairs)
        for s in EmptyClauseSet.objects.prefetch_related("clauses")
    ]


def _is_pruned(candidate: frozenset, empty_sets) -> bool:
    """Is this candidate a superset of a conjunction already known to be empty?

    The anti-monotone rule, and the whole return on probing: adding clauses can
    only ever remove rows, so a candidate containing an empty set is empty too and
    never needs a call of its own.
    """
    return any(empty <= candidate for empty in empty_sets)


def _record_empty(clauses) -> None:
    """Blacklist a conjunction its probe found empty. Idempotent."""
    entry, created = EmptyClauseSet.objects.get_or_create(clause_key=clause_key(clauses))
    if created:
        entry.clauses.set(Clause.rows_for(clauses))


# ── the descent ──────────────────────────────────────────────────────

def _candidates(campaign, pool: dict[str, list[str]]) -> list[list[tuple[str, str]]]:
    """Every conjunction the live pool spans, deepest and closest-to-seed first.

    One value per family, every family present — an OR is unrepresentable here by
    construction (that is the point of composing from clauses rather than asking
    for a filter dict). A family whose values all died in the sweep simply drops
    out, so candidates stay as deep as the surviving pool allows.

    Ordered by how far each candidate sits from the pool's head — the value the
    seed took in each family — because the ICP's own ranking is the only prior a
    wall has. Every candidate is the same depth, so ordering is a heuristic and
    nothing rests on it: the counts sort the regions out once they are fetched.
    """
    families = sorted(pool)
    ranks = {family: {v: i for i, v in enumerate(pool[family])} for family in families}
    combos = itertools.product(*(pool[family] for family in families))
    candidates = [
        sorted(zip(families, values)) for values in combos
    ]
    candidates.sort(key=lambda c: (
        sum(ranks[family][value] for family, value in c), c,
    ))
    return candidates


def _tried_keys(campaign) -> set[str]:
    """Clause-set keys this campaign has already fetched a page of."""
    return set(
        DiscoveryQuery.objects
        .filter(campaign=campaign)
        .values_list("clause_key", flat=True)
    )


def descend(campaign) -> list[tuple[str, str]]:
    """The next untried, verified non-empty conjunction from the pool, or ``[]``.

    The wall move, as a lookup: sweep any unprobed clause out of the pool, then walk
    the conjunctions it spans until one probes non-empty, and hand that to the
    frontier to fetch. Empty ones are blacklisted on the way past, so the walk never
    pays for them twice and their supersets are pruned for free.

    ``[]`` means the descent is genuinely out of conjunctions — every one the pool
    spans is fetched or empty. That is the *only* condition under which the LLM is
    asked again (``mutate.descend_or_refill``); it is not a failure.
    """
    pool = _live_pool(campaign)
    if not pool:
        return []

    if _sweep_singletons(campaign):
        pool = _live_pool(campaign)  # the sweep may have retired clauses, or a whole family

    tried = _tried_keys(campaign)
    empty_sets = _empty_sets()
    probed = 0
    for candidate in _candidates(campaign, pool):
        if clause_key(candidate) in tried or _is_pruned(frozenset(candidate), empty_sets):
            continue
        probed += 1
        if probe(candidate):
            logger.info("[%s] %s after %d probe(s): %s", campaign,
                        colored("descent", "yellow", attrs=["bold"]), probed,
                        colored(describe_clauses(candidate), "cyan"))
            return candidate
        _record_empty(candidate)
        empty_sets.append(frozenset(candidate))

    logger.info("[%s] %s: every conjunction the pool spans is fetched or empty "
                "(%d probe(s) this move)", campaign,
                colored("descent exhausted", "yellow", attrs=["bold"]), probed)
    return []
