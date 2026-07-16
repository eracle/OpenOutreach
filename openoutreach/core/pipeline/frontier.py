# openoutreach/core/pipeline/frontier.py
"""Discovery frontier — a lazy best-first walk over a campaign's query nodes.

Replaces the single ``(Campaign.icp_filters, discovery_offset)`` cursor. There is
no persisted queue of candidates: the next query is computed **lazily** from the
set of already-fetched ``DiscoveryQuery`` nodes, like the ``pools.py`` generator
chain. One discovery *move* (``discover.py``):

    next_query → fetch → persist_fetched
         │                    └ empty page → mark_exhausted
         └ cold start → icp.generate_seed

``next_query`` picks exactly one query to fetch, scored by **ground truth**: each
node's ``(examined, qualified)`` counts over its first-touch leads' deals, computed
by ``node_stats`` and never stored. The three moves, and the regime that selects
them, fall straight out of those two counts:

- **Bootstrap** *(no node has an examined lead — nothing is rankable)* — page the
  seed linearly, exactly like the old cursor, feeding qualification the labels that
  train the GP.
- **Deepen** *(the best rankable node has ``qualified > 0``)* — deepen its line to
  mine a productive vein.
- **Wall** *(every rankable node has ``qualified == 0``)* — ask the LLM for one new
  query.

**The frontier reads no signal from the GP.** It used to: nodes were scored by how
many of their leads cleared ``min_gp_confidence`` (0.9 — a *spend* gate), and the
regime was the qualifier's ``n_neg > n_pos`` **balance-driven acquisition strategy**
read as if it were a competence gate. Neither measured what the walk needed. At a
realistic base rate negatives outnumber positives forever, so the walk never left
bootstrap; and no unlabelled lead ever clears 0.9, because a fitted GP reproduces
its training points (0.755–0.829 measured) and regresses everything it has never
seen toward the prior (0.121–0.327) — the two populations do not overlap, so a bar
drawn from one is unreachable by the other by construction. Every node scored 0, the
walk read a permanent wall, and ``deepen`` never fired once. Three jobs, three
mechanisms: the frontier steers on node counts, the GP/BALD picks which lead to
qualify next, and ``min_gp_confidence`` is *only* the spend gate.

**``examined == 0`` means unknown, not barren**, so an unsampled node is not
rankable and cannot vote for a wall. Exhaustion is reactive: a fetch that returns an
empty page marks that clause set exhausted so it is never re-picked. No explore-share
cadence, no width target, no size cap — the walk widens exactly when the regions it
can actually see stop paying.

See the roadmap card ``p2-e3-discovery-query-graph-search``.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections import namedtuple

from django.db.models import Count, Max, Q

from openoutreach.core.models import Clause, DiscoveryQuery

logger = logging.getLogger(__name__)

DISCOVERY_PAGE_SIZE = 100

# The query to fetch next, plus which move produced it — ``bootstrap``, ``deepen``,
# or ``wall``. ``clauses`` is the clause set as ``(family, value)`` pairs; nothing is
# persisted until the fetch returns rows. ``discover`` uses ``move`` for logging and
# to cap a move at a single wall fetch (a freshly opened region that comes back
# empty ends the move).
NextQuery = namedtuple("NextQuery", ["clauses", "offset", "move"])


# ── clause-set identity ──────────────────────────────────────────────

def canonicalize(clauses) -> str:
    """Deterministic text for a clause set — sorted ``family=value`` pairs."""
    return json.dumps(sorted(clauses), separators=(",", ":"))


def clause_key(clauses) -> str:
    """sha256 of the canonicalized clause set — the node-identity key for dedup.

    Order-independent, so the same conjunction reached by two different descents is
    one node.
    """
    return hashlib.sha256(canonicalize(clauses).encode()).hexdigest()


# ── persist / exhaust ────────────────────────────────────────────────

def _clauses_for(clauses) -> list[Clause]:
    """Get-or-create the ``Clause`` rows for ``clauses``. Idempotent; clauses are shared."""
    return [
        Clause.objects.get_or_create(family=family, value=value)[0]
        for family, value in clauses
    ]


def persist_fetched(campaign, clauses, offset: int) -> DiscoveryQuery:
    """Record a just-fetched ``(clause set, offset)`` page, deduped on the triple.

    Creates any ``Clause`` row that doesn't exist yet, so a node always points at
    real clauses. Returns the node (created or pre-existing) so its first-touch leads
    can point back via ``Lead.discovered_by`` — the link that lets ``node_stats``
    count what the node was worth, once qualification has ruled on those leads.
    """
    node, created = DiscoveryQuery.objects.get_or_create(
        campaign=campaign, clause_key=clause_key(clauses), offset=offset,
    )
    if created:
        node.clauses.set(_clauses_for(clauses))
    return node


def mark_exhausted(campaign, clauses) -> None:
    """Flag every node of a clause-set line exhausted — its fetch hit an empty page.

    A whole query line is exhausted at once (all offsets share the fate of the
    deepest, dry page), so it drops out of ``next_query`` and is never re-picked.
    Emptiness is the **only** thing that retires a line: a barren *yield* (leads that
    exist but don't qualify) is a verdict about a view, not about the query.
    """
    DiscoveryQuery.objects.filter(
        campaign=campaign, clause_key=clause_key(clauses),
    ).update(exhausted=True)


# ── the node metric ──────────────────────────────────────────────────

# What a node is worth, measured rather than predicted. ``examined`` is how many of
# its first-touch leads the LLM has ruled on; ``qualified`` how many it accepted.
# Both are needed: ``qualified == 0`` at ``examined == 0`` is *unknown*, and must
# never sort as barren.
NodeStats = namedtuple("NodeStats", ["examined", "qualified"])


def node_stats(campaign) -> dict[int, NodeStats]:
    """``{node_pk: NodeStats}`` for the campaign — one ``GROUP BY``, nothing stored.

    A node's value is the count of qualified leads among the ones it first touched.
    Denominators are comparable because every fetch is a full ``DISCOVERY_PAGE_SIZE``
    page (a short final page inflates only the vein we are about to exhaust anyway).

    ``qualified`` counts the leads the **LLM accepted**, which is *not*
    ``state == QUALIFIED``: that is a snapshot of a funnel a lead moves through, so
    counting it would make a node's score *fall* as its leads succeed into
    READY_TO_EMAIL and beyond — the walk would abandon a vein exactly when it starts
    paying. An accepted lead is any deal that is not an LLM rejection, mirroring
    ``Lead.get_labeled_arrays``' rule (``FAILED`` + ``wrong_fit`` is the rejection;
    a ``FAILED`` deal with any other outcome is an *operational* failure — "no email"
    — of a lead the LLM said yes to).

    Nodes with no deals are absent from the mapping: never examined, so unknown.
    """
    from openoutreach.crm.models import Deal, DealState, Outcome

    rows = (
        Deal.objects
        .filter(campaign=campaign, lead__discovered_by__isnull=False)
        .values("lead__discovered_by")
        .annotate(
            examined=Count("pk"),
            qualified=Count("pk", filter=~Q(
                state=DealState.FAILED, outcome=Outcome.WRONG_FIT,
            )),
        )
    )
    return {
        r["lead__discovered_by"]: NodeStats(r["examined"], r["qualified"])
        for r in rows
    }


# ── selection ────────────────────────────────────────────────────────

def _next_offset(campaign, key: str) -> int:
    """The next unfetched offset for a clause-set line — max fetched offset + one
    page, or 0 if the line has never been fetched."""
    deepest = (
        DiscoveryQuery.objects
        .filter(campaign=campaign, clause_key=key)
        .aggregate(m=Max("offset"))["m"]
    )
    return 0 if deepest is None else deepest + DISCOVERY_PAGE_SIZE


def _bootstrap_query(campaign) -> NextQuery | None:
    """The next seed page to fetch while nothing is rankable — the old linear cursor.

    The seed is the earliest node, so it *is* the line we page. Deepen it; on a cold
    start (no node yet) the ICP generates the seed and we start at offset 0.
    """
    from openoutreach.core.pipeline.icp import generate_seed

    seed = DiscoveryQuery.objects.filter(campaign=campaign).order_by("pk").first()
    if seed is None:
        clauses = generate_seed(campaign)
        return NextQuery(clauses, 0, "bootstrap") if clauses else None
    if seed.exhausted:
        return None  # the seed line dried up before anything else was measurable
    return NextQuery(seed.clause_pairs, _next_offset(campaign, seed.clause_key), "bootstrap")


def next_query(campaign) -> NextQuery | None:
    """The single query to fetch next, or None when nothing is left to walk.

    The regime is decided by ``node_stats`` alone — there is no gate to tune:

    - **Bootstrap** (no active node has an examined lead): page the seed linearly.
      Nothing has been ruled on, so we stay on our best prior until qualification
      says otherwise. On a cold start (no nodes yet) the seed is generated from the
      ICP; thereafter the seed node itself carries its clauses. None if the ICP is
      empty or the seed line has dried up (nothing measured to move toward).
    - **Deepen** (the best rankable node has ``qualified > 0``): deepen its line.
    - **Wall** (every rankable node has ``qualified == 0``): ask the LLM for one new
      query at offset 0. None if the LLM is dry or re-proposes an already-tried
      query.

    Unexamined nodes are skipped rather than scored zero, so a region nobody has
    looked at can neither be deepened nor counted as a wall.
    """
    active = list(DiscoveryQuery.objects.filter(campaign=campaign, exhausted=False))
    stats = node_stats(campaign)
    rankable = [(n, stats[n.pk]) for n in active if stats.get(n.pk, NodeStats(0, 0)).examined]
    if not rankable:
        return _bootstrap_query(campaign)

    best = max(
        ((n, s) for n, s in rankable if s.qualified > 0),
        key=lambda pair: (pair[1].qualified, pair[0].pk), default=None,
    )
    if best is not None:
        node = best[0]
        return NextQuery(node.clause_pairs, _next_offset(campaign, node.clause_key), "deepen")

    # Wall — every region we can see has been ruled on and pays nothing. Open a new
    # one. A wall is a *yield* verdict, so it retires nothing: only an empty page
    # (``mark_exhausted``) takes a line out of the walk.
    from openoutreach.core.pipeline.mutate import generate_mutation

    clauses = generate_mutation(campaign)
    if not clauses:
        return None
    if DiscoveryQuery.objects.filter(
        campaign=campaign, clause_key=clause_key(clauses),
    ).exists():
        return None  # LLM re-proposed a query we already tried — nothing new to open
    return NextQuery(clauses, 0, "wall")
