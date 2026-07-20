# openoutreach/core/pipeline/frontier.py
"""Discovery frontier — a lazy best-first walk over a campaign's query nodes.

No persisted queue: the next query is computed from the already-fetched
``DiscoveryQuery`` rows. One move (``discover.py``) is ``next_query → fetch →
persist_fetched``; a cold start seeds the pool via ``icp.generate_seed`` first.

``next_query`` has two moves and interleaves them **1:1**:

- **Deepen** — page a productive conjunction one offset deeper. Productive means
  ``qualified > 0`` summed over *all* its offsets (``line_stats``, grouped by
  ``clause_key``), so a lead qualified at offset 0 keeps paging the line deeper and
  does not expire when one deep page qualifies nobody.
- **Visit** — the deepest unfetched conjunction from ``descend`` (widening only below
  an empty query), and an LLM refill once the pool spans nothing new.

The 1:1 balance is stateless strict alternation: a deepen leaves a page at
``offset > 0``, a visit a conjunction at ``offset == 0``, so the newest node's offset
says which move came last and ``next_query`` takes the other. Deepen is unavailable
until something qualifies, so cold-start moves are all visits and the first deepen
fires the move after the first label lands — no catch-up burst.

Per-line deepen paging from a line's high-water mark once ran forever on a broad
singleton (``headcount 1–?``, never empties). It is safe now because ``descend`` is
deepest-only — the only lines that exist are deep conjunctions, which empty in bounded
pages — and 1:1 caps any one line at half the moves.

The walk reads **no GP signal**: it steers on counted deals, never on
``min_gp_confidence`` (a spend gate) or the qualifier's acquisition balance. A just-
fetched page has ``examined == 0`` and does not vote — unknown is not barren.

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


# ── the query metric ─────────────────────────────────────────────────

# What a query is worth, measured rather than predicted. ``examined`` is how many of
# its first-touch leads the LLM has ruled on; ``qualified`` how many it accepted
# (any deal that is not an LLM rejection — ``FAILED`` + ``wrong_fit`` — mirroring
# ``Lead.get_labeled_arrays``). Counting ``state == QUALIFIED`` would make the value
# fall as leads succeed down the funnel, so a paying vein would read barren. Both
# fields are needed: ``qualified == 0`` at ``examined == 0`` is *unknown*, not barren.
NodeStats = namedtuple("NodeStats", ["examined", "qualified"])


def _deal_counts(campaign, group_by: str) -> dict:
    """``{group value: NodeStats}`` over first-touch leads' deals — one ``GROUP BY``.

    ``group_by`` is a field path on ``Deal`` (e.g. the discovering node, or its
    clause set). Groups with no deals are absent: never examined, so unknown.
    """
    from openoutreach.crm.models import Deal, DealState, Outcome

    rows = (
        Deal.objects
        .filter(campaign=campaign, lead__discovered_by__isnull=False)
        .values(group_by)
        .annotate(
            examined=Count("pk"),
            qualified=Count("pk", filter=~Q(
                state=DealState.FAILED, outcome=Outcome.WRONG_FIT,
            )),
        )
    )
    return {r[group_by]: NodeStats(r["examined"], r["qualified"]) for r in rows}


def node_stats(campaign) -> dict[int, NodeStats]:
    """``{node_pk: NodeStats}`` — value per fetched page. Used by the LLM-refill prompt."""
    return _deal_counts(campaign, "lead__discovered_by")


def line_stats(campaign) -> dict[str, NodeStats]:
    """``{clause_key: NodeStats}`` — value per conjunction, summed over its offsets.

    The deepen metric: a lead qualified at any offset counts for the whole line, so
    one paying page keeps the line paging deeper instead of expiring per page.
    """
    return _deal_counts(campaign, "lead__discovered_by__clause_key")


# ── selection ────────────────────────────────────────────────────────

def _productive_line(campaign) -> NextQuery | None:
    """Deepen the most-qualified open conjunction, paged from its deepest offset.

    A conjunction is a line of pages sharing a ``clause_key``; its value is the
    qualified count summed over all of them (``line_stats``). The winner is the open
    (non-exhausted) line with the most qualified leads, paged one ``DISCOVERY_PAGE_SIZE``
    past its high-water offset. Ties break toward the oldest line (nearest the seed).
    None when nothing has qualified yet.
    """
    stats = line_stats(campaign)
    lines: dict[str, dict] = {}
    for node in DiscoveryQuery.objects.filter(campaign=campaign):
        line = lines.get(node.clause_key)
        if line is None:
            lines[node.clause_key] = {"top": node, "max_offset": node.offset,
                                      "exhausted": node.exhausted}
        else:
            line["max_offset"] = max(line["max_offset"], node.offset)
            line["exhausted"] = line["exhausted"] or node.exhausted
            if node.pk < line["top"].pk:
                line["top"] = node

    open_lines = [
        (key, line) for key, line in lines.items()
        if not line["exhausted"] and stats.get(key, NodeStats(0, 0)).qualified > 0
    ]
    if not open_lines:
        return None
    key, line = max(open_lines, key=lambda kv: (stats[kv[0]].qualified, -kv[1]["top"].pk))
    return NextQuery(line["top"].clause_pairs, line["max_offset"] + DISCOVERY_PAGE_SIZE, "deepen")


def _visit_move(campaign) -> NextQuery | None:
    """Open the next conjunction from the composer, or None if it has nothing new.

    None when the composer is dry or re-proposes a query already fetched.
    """
    from openoutreach.core.pipeline.mutate import generate_mutation

    clauses = generate_mutation(campaign)
    if not clauses:
        return None
    if DiscoveryQuery.objects.filter(campaign=campaign, clause_key=clause_key(clauses)).exists():
        return None
    return NextQuery(clauses, 0, "visit")


def next_query(campaign) -> NextQuery | None:
    """The single query to fetch next, alternating deepen and visit 1:1, or None.

    Strict alternation off the last move: a deepen leaves a page at ``offset > 0``, a
    visit a conjunction at ``offset == 0``, so the newest node's offset says which came
    last and this move takes the other — falling back when its own side is empty
    (deepen is unavailable until something qualifies, so cold-start moves are all
    visits). On a cold start the ICP seeds the pool first (``generate_seed``).
    """
    from openoutreach.core.pipeline.icp import generate_seed

    deepen = _productive_line(campaign)
    last_offset = (
        DiscoveryQuery.objects.filter(campaign=campaign)
        .order_by("-pk").values_list("offset", flat=True).first()
    )
    last_was_deepen = last_offset is not None and last_offset > 0

    if deepen is not None and not last_was_deepen:
        return deepen

    if not campaign.clauses.exists():  # deepen needs no pool; visit composes from it
        generate_seed(campaign)
    return _visit_move(campaign) or deepen
