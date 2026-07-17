# openoutreach/core/pipeline/frontier.py
"""Discovery frontier — a lazy best-first walk over a campaign's query nodes.

Replaces the single ``(Campaign.icp_filters, discovery_offset)`` cursor. There is
no persisted queue of candidates: the next query is computed **lazily** from the
set of already-fetched ``DiscoveryQuery`` nodes, like the ``pools.py`` generator
chain. One discovery *move* (``discover.py``):

    next_query → fetch → persist_fetched
         │                    └ empty page → mark_exhausted
         └ cold start → icp.generate_seed

``next_query`` picks exactly one query to fetch, and there are only two things it
can say:

- **Deepen** *(some node has ``qualified > 0`` and its next page is unfetched)* —
  page one deeper. A region that has produced a qualified lead is the only evidence
  the walk ever gets that it is somewhere worth being, so it outranks the structural
  visit outright — but the evidence is **re-earned at every depth**: a node earns
  only *its own* next page, so one paying page buys exactly one more page, never a
  licence to run.
- **Visit** *(nothing has qualified yet)* — hand back the next unvisited conjunction
  in ``descend``'s lattice order (level 1 → N → N-1 → … → 2), and when the pool
  spans nothing new, ask the LLM for fresh clauses.

That is the whole regime. It reads **one** number — ``qualified`` from
``node_stats``, counted over each node's first-touch leads' deals — and it only ever
asks whether that number is positive. There is no bar to tune and no ranking of
barren nodes against each other, because a node with no qualified leads is not
*bad*, it is just not yet a reason to stop walking.

**Deepen votes per node, never per line, and this is load-bearing.** The metric was
always per ``(clause set, offset)`` — a node *is* a page — but deepen used to elect
the best node *anywhere* in a line and then page from the line's high-water mark.
Those are two different keys, and the gap between them is a non-terminating walk: an
offset-0 page that qualified 5 leads keeps that 5 forever, so it keeps winning the
election, while the offset it hands back climbs 100, 200, 300 … with nothing at depth
ever asked whether it paid. The line's own emptiness is the only brake, and a level-1
singleton like ``headcount 1–?`` matches millions of rows, so that page never comes:
the walk mines the broadest query in the lattice forever, harvesting the provider's
famous-company head (the exact failure ``descend`` orders itself to avoid) and never
visiting the other conjunctions at all. The fix reads the *node's own* count and hands
back the *node's own* next page: a page that qualified elects exactly the page after
it, and only until that page exists — so evidence is local to the depth that produced
it, which is where it was measured, and no clause-set line is ever grouped to vote as
a whole.

A just-fetched frontier page has ``examined == 0`` and so does not vote — the walk
falls back to ``visit`` until qualification rules on it. That is the ``examined == 0``
is *unknown, not barren* distinction, and here it costs nothing: the deepen is not
lost, only deferred to the move after the labels land, and the walk does structural
work meanwhile instead of paging blind. Deepen firing once per new evidence rather
than in a run is the point, not a regression.

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

An earlier design also carried a **bootstrap** regime. It is gone and nothing was
lost: bootstrap paged the seed because the visit had no order of its own, and the
seed is now simply the head of level N.

Exhaustion is reactive: a fetch that returns an empty page marks that clause set
exhausted so it is never re-picked, and an empty page **at offset 0** additionally
records the conjunction as an ``EmptyClauseSet``, pruning every superset of it for
free. No explore-share cadence, no width target, no size cap.

See the roadmap card ``p2-e3-discovery-query-graph-search``.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections import namedtuple

from django.db.models import Count, Q

from openoutreach.core.models import Clause, DiscoveryQuery, EmptyClauseSet

logger = logging.getLogger(__name__)

DISCOVERY_PAGE_SIZE = 100

# The query to fetch next, plus which move produced it — ``deepen`` or ``visit``.
# ``clauses`` is the clause set as ``(family, value)`` pairs; nothing is persisted
# until the fetch returns rows. ``discover`` uses ``move`` for logging and to cap a
# move at a single fresh visit (a newly opened region that comes back empty ends the
# move rather than walking the lattice at ~45s a node inside one call).
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
        node.clauses.set(Clause.rows_for(clauses))
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


def record_empty(clauses) -> None:
    """Blacklist a conjunction the index matched nobody with. Idempotent.

    Written by the leg that *learns* it — ``discover``, on an empty page at offset 0
    — and read by ``descend._is_pruned`` as the anti-monotone rule: a candidate is
    dead iff some recorded set is a subset of it. One dry fetch therefore retires a
    whole sublattice without another call.

    **Only an offset-0 empty page belongs here.** An empty page deeper in a line
    means a vein ran out, not that the conjunction matches nobody — recording that
    would convict a query that has already produced leads.

    **Global, with no campaign FK**, like ``Clause``: emptiness is a fact about the
    provider's index, not about a campaign, so one campaign's dry fetch prunes every
    campaign's lattice for free.
    """
    entry, created = EmptyClauseSet.objects.get_or_create(clause_key=clause_key(clauses))
    if created:
        entry.clauses.set(Clause.rows_for(clauses))


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

def _productive_node(campaign) -> DiscoveryQuery | None:
    """The node with the most qualified leads whose next page is still unfetched.

    The walk's one piece of positive evidence, read on the exact ``(clause set,
    offset)`` **node** that earned it — never on the clause-set line as a whole. A node
    earns *only its own* successor page: a shallower page that once qualified cannot
    keep voting, because the page it would open (``offset + one``) has already been
    fetched, so it drops out and the still-open frontier is what remains. That is what
    keeps one paying page from buying an unbounded descent.

    Ties break on ``pk`` — the older node, which is the one closer to the ICP's own
    ranking.
    """
    stats = node_stats(campaign)
    nodes = list(DiscoveryQuery.objects.filter(campaign=campaign))
    fetched = {(node.clause_key, node.offset) for node in nodes}
    scored = [
        (node, stats[node.pk].qualified)
        for node in nodes
        if not node.exhausted
        and stats.get(node.pk, NodeStats(0, 0)).qualified > 0
        and (node.clause_key, node.offset + DISCOVERY_PAGE_SIZE) not in fetched
    ]
    if not scored:
        return None
    return max(scored, key=lambda pair: (pair[1], -pair[0].pk))[0]


def next_query(campaign) -> NextQuery | None:
    """The single query to fetch next, or None when nothing is left to walk.

    Two moves, and the first one that applies wins:

    - **Deepen** (some node has ``qualified > 0`` and its next page is unfetched):
      page that node one deeper. A region that has produced a qualified lead is the
      only evidence the walk gets that it is somewhere worth being, so it pre-empts the
      structural visit for as long as it keeps paying — but *keeps paying* is asked of
      each page on its own, so a vein that stops qualifying releases the walk without
      waiting for the empty page that ``mark_exhausted`` needs.
    - **Visit** (nothing has qualified yet): the next unvisited conjunction from
      ``descend``, at offset 0, and an LLM refill once the pool spans nothing new.
      None if the LLM is dry or re-proposes a query already fetched.

    On a cold start the ICP seeds the pool first — ``generate_seed`` persists every
    candidate value it produced, and the seed conjunction it returns needs no special
    case here because the visit order makes it the head of level N anyway.
    """
    from openoutreach.core.pipeline.icp import generate_seed
    from openoutreach.core.pipeline.mutate import generate_mutation

    node = _productive_node(campaign)
    if node is not None:
        return NextQuery(node.clause_pairs, node.offset + DISCOVERY_PAGE_SIZE, "deepen")

    if not campaign.clauses.exists():
        generate_seed(campaign)

    clauses = generate_mutation(campaign)
    if not clauses:
        return None
    if DiscoveryQuery.objects.filter(
        campaign=campaign, clause_key=clause_key(clauses),
    ).exists():
        return None  # re-proposed a query we already fetched — nothing new to open
    return NextQuery(clauses, 0, "visit")
