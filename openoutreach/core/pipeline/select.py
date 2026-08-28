# openoutreach/core/pipeline/select.py
"""Query selection — the frontier is counted, drawn from, and fired. No model involved.

The whole discovery walk, from first principles. A **node** is a set of ``(field, token)``
keywords; firing it pages Lead Finder for the conjunction. Its **children** are itself
plus one more token. There is no remove move: the frontier is global — every unfired child
of every fired node, all in one pool — so a shallow node's untried siblings are always
still reachable and removal would be a second way to say the same thing.

**A node's value is arithmetic over labels.**

    P̂(node) = (a + 2·P̂(parent)) / (a + b + 2)

where ``a``/``b`` are the qualified/rejected leads in the store whose profile contains all
of this node's tokens. That is ordinary Laplace smoothing with the prior pointed at the
**parent's rate** instead of at 0.5, which is the whole idea: the parent supplies the
level, the child's own counts move it off, and a child with thin evidence stays near its
parent rather than swinging to 0 or 1.

**Why not the GP.** It used to score candidates by embedding their keywords. Measured
head-to-head on 4,100 parent→child edges with the GP fit on one half of the labels and
every truth measured on the other, the counts win outright and the GP adds *nothing* on
top of them:

    child counts smoothed to parent   pearson 0.661     P(parent) only        0.567
    P(child) counts alone             0.653             GP(child) alone       0.450
    counts + 0.15·GP delta            0.660             P(parent) + GP delta  0.452

The GP is not gone from the system — it still qualifies leads, and it is what produces the
``a``/``b`` this module counts. It is gone from *choosing queries*. The residual-anchoring
idea (``P(parent) + λ·GP delta``) was measured too: real, but worth 0.02 pearson at
λ≈0.15, and worse than doing nothing at λ=1. See §13 of the roadmap card
``p1-e3-leadfinder-index-semantics-and-query-model-rethink``.

**Sampling is Thompson over the same two numbers.** ``θ ~ Beta(a + 2·P̂(parent), b +
2·(1−P̂(parent)))`` — the Beta parameters *are* the smoothed estimate, so this is one line
rather than a mechanism, and there is no constant to tune. Width tracks evidence:
``Beta(141, 3)`` is razor-tight and ``Beta(2, 1)`` is wide, so a node nobody has tried gets
tried and a node measured bad three times stops appearing. Set ``THOMPSON = False`` to fall
back to greedy — defensible here, because depletion pushes the walk along on its own, and
which is better cannot be settled offline (simulating it needs corpus answers for queries
never fired).

**Retirement is a corpus fact, never a model fact.** Nothing is retired for scoring badly —
the qualifier refits constantly, and a barren yield is a verdict about a view, not about
whether anybody matches. Only emptiness retires a node, and *which* emptiness depends on
the offset, because the provider answers ``0`` both for a query matching nobody and for one
paged past its end (§7).
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter

import numpy as np

from openoutreach.discovery import describe_filters, filters_for

logger = logging.getLogger(__name__)

DISCOVERY_PAGE_SIZE = 100

# How much of the frontier a debug run prints. The frontier is the whole decision, but
# printing thousands of rows per pass buries the answer it is meant to explain.
_DEBUG_FRONTIER_ROWS = 10

# Elasticsearch's ``index.max_result_window``, measured (§7): every query is worth at most
# 10,000 rows however many millions it counts. Reaching it means "capped", which is a very
# different fact from "drained" — a capped node's children still open fresh windows, a
# drained node's children are already in our DB.
REACH_CAP = 10_000

# Draw the frontier's order from each node's Beta, rather than taking its mean. One line,
# nothing to tune; see the module docstring.
THOMPSON = True

# How many tokens one field may hold in a single node. Same-field tokens are space-joined
# and the provider ANDs the words *inside* that field, so a third token asks for a job
# title carrying all three words — which essentially nobody's does. Two is where the
# measurement sits (``"founder cto"`` counts 9,027 at near-perfect precision), and without
# this the walk descends: a live campaign reached 183 frontier nodes at depth 4 asking for
# titles like ``ai-powered cto founder user``, each costing a provider round-trip to learn
# it matches nobody. The cap applies per field, so a node can still narrow on another axis.
MAX_TOKENS_PER_FIELD = 2


# ── node identity ────────────────────────────────────────────────────

def token_key(keywords) -> str:
    """sha256 of the canonicalized keyword set — the node-identity key.

    Order-independent and field-aware, so ``{title:founder, title:cto}`` is one node
    whichever parent reached it first. Add-only expansion over three fields means most
    nodes *are* reachable several ways, so this is load-bearing rather than a dedup nicety.
    """
    canonical = json.dumps(sorted(tuple(k) for k in keywords), separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


# ── the label store ──────────────────────────────────────────────────

class LabelStore:
    """Every labelled profile as a token set plus a verdict — the walk's whole evidence.

    Loaded once per discovery pass and held in memory: the store is hundreds of rows, so
    counting a node is a set-containment scan that costs microseconds, and re-deriving it
    each pass means there is no counter anywhere to drift out of step with the labels.
    """

    def __init__(self, tokens: list[frozenset[str]], labels: list[int]):
        self._tokens = tokens
        self._labels = labels

    @classmethod
    def load(cls, campaign) -> "LabelStore":
        """The campaign's labelled leads, **plus the anchors as positives**.

        Qualified = any deal that is not a rejection.

        **The anchors are what make the cold phase work at all**, here for the same
        reason they already anchor the GP. Expansion only offers a token that has shared
        a *qualified* profile with the node, and a campaign that has never accepted
        anybody has no qualified profile — so the frontier could not grow past its
        depth-1 seed nodes, which are one-token queries matching millions of the wrong
        people, so nothing qualified, so the frontier still could not grow. A closed
        loop, and the seed's own tokens could never be conjoined into the precise query
        (``"founder cto"``, not ``founder``) that the walk exists to find.

        The synthetic ideal profiles break it: they are written in ``profile_text``'s
        shape, so they tokenize like any lead and say which words describe the people
        this campaign wants — which is exactly the claim co-occurrence needs. It is the
        same bargain the GP already takes, on the same evidence: ``BayesianQualifier``
        keeps the anchor profiles permanently, so this store always counts them alongside
        whatever real qualified profiles have accumulated — a small, constant nudge that
        matters early and is swamped by real evidence as the campaign grows.

        They deliberately do **not** feed the vocabulary (``vocabulary.refresh``): an
        anchor is one flat string with no per-field structure, and splitting it by guess
        would file ``united states`` as a job title — the error
        ``discovery.KEYWORD_SOURCE_FIELDS`` exists to prevent. Anchors say which words go
        *together*; only a real lead row says which field a word is searchable in.
        """
        from openoutreach.core.pipeline.vocabulary import profile_tokens
        from openoutreach.crm.models import Deal, DealState, Lead, Outcome

        verdicts = {}
        for lead_id, state, outcome in (
            Deal.objects.filter(campaign=campaign, lead_id__isnull=False)
            .values_list("lead_id", "state", "outcome")
        ):
            verdicts[lead_id] = 0 if (state == DealState.FAILED
                                      and outcome == Outcome.WRONG_FIT) else 1

        tokens, labels = [], []
        for lead_id, text in (
            Lead.objects.filter(pk__in=verdicts).values_list("pk", "profile_text")
        ):
            if text:
                tokens.append(profile_tokens(text))
                labels.append(verdicts[lead_id])

        anchors = 0
        for profile in campaign.anchor_profiles or []:
            if profile:
                tokens.append(profile_tokens(profile))
                labels.append(1)
                anchors += 1
        if anchors:
            logger.debug("[%s] label store: %d real verdict(s) + %d anchor(s) as positives",
                         campaign, len(labels) - anchors, anchors)
        return cls(tokens, labels)

    def __len__(self) -> int:
        return len(self._labels)

    @property
    def qualified_count(self) -> int:
        """How many labelled profiles were accepted — what expansion has to work from."""
        return sum(self._labels)

    @property
    def base_rate(self) -> float:
        """The level a depth-1 node inherits — what an unfiltered query would qualify at.

        The root's stand-in. The walk deliberately never *fires* the empty query (it
        matches everyone, and its 10k window is the provider's famous-company head), so
        the one thing a root would have supplied is taken from the labels instead.

        **Laplace-smoothed, and that is load-bearing rather than tidy.** The raw rate is 0
        for a campaign whose every verdict so far is a rejection — the exact state the
        anchors exist for, so a common one, not an edge case — and a level of 0 makes
        ``α = a + 2·0`` zero for every unlabelled node, which is not a distribution.
        Smoothing here fixes it for the whole walk by induction: with the level strictly
        inside (0, 1), every ``_beta_params`` is positive on both sides, so every
        ``estimate`` is again strictly inside (0, 1) for the children below it. An empty
        store still reads 0.5.
        """
        return (sum(self._labels) + 1) / (len(self._labels) + 2)

    def counts(self, pairs) -> tuple[int, int]:
        """``(a, b)`` — labelled profiles containing every one of a node's tokens.

        Field-agnostic (see ``vocabulary.profile_tokens``): a node's tokens are matched
        anywhere in the profile, which is how the estimator was measured and what lets
        rows predating per-field capture still count.
        """
        wanted = {token for _, token in pairs}
        a = b = 0
        for tokens, label in zip(self._tokens, self._labels):
            if wanted <= tokens:
                if label:
                    a += 1
                else:
                    b += 1
        return a, b

    def cooccurring(self, pairs, candidates) -> list[tuple[str, str]]:
        """Candidate keywords that appear alongside ``pairs`` in ≥1 *qualified* profile.

        The expansion rule, and the reason the frontier stays small without a top-K cap.
        A child whose tokens never co-occur in anything we have accepted is a guess the
        store cannot speak to at all: it would enter with ``a = b = 0``, inherit its
        parent's estimate exactly, and be indistinguishable from every one of its
        thousand siblings — so the draw among them would be noise, and most would be
        empty at the provider anyway. Requiring one real co-occurrence keeps every child
        a proposition the evidence has something to say about, and it self-limits with
        depth: a three-token node has few words that ever shared a profile with it.
        """
        wanted = {token for _, token in pairs}
        live = set()
        for tokens, label in zip(self._tokens, self._labels):
            if label and wanted <= tokens:
                live |= tokens
        return [(field, token) for field, token in candidates
                if token in live and (field, token) not in set(pairs)]


# ── the estimator ────────────────────────────────────────────────────

def _beta_params(node, store: LabelStore, cache: dict) -> tuple[float, float]:
    """``(α, β)`` of a node's Beta — the smoothed estimate, in the form Thompson wants.

    Two pseudo-counts of prior mass pointed at the parent's rate. The budget of 2 is
    ordinary Laplace, not a tuned knob: it is the same "one imagined success and one
    imagined failure" that keeps a zero-count node from reading as certain.

    Both sides are strictly positive because the level is (see ``LabelStore.base_rate``),
    which is what makes the result a usable Beta rather than a degenerate one.
    """
    a, b = store.counts(node.pairs)
    level = estimate(node.parent, store, cache) if node.parent_id else store.base_rate
    return a + 2 * level, b + 2 * (1 - level)


def estimate(node, store: LabelStore, cache: dict | None = None) -> float:
    """``P̂(node)`` — the node's smoothed qualification rate.

    Recurses to the parent for the level. Depth is the number of tokens in a node, so the
    recursion is a handful of frames; the cache keeps a frontier of thousands from
    re-walking shared ancestry.
    """
    cache = {} if cache is None else cache
    if node is None:
        return store.base_rate
    if node.pk in cache:
        return cache[node.pk]
    alpha, beta = _beta_params(node, store, cache)
    cache[node.pk] = value = alpha / (alpha + beta)
    return value


# ── the frontier ─────────────────────────────────────────────────────

def frontier(campaign) -> list:
    """Every node still worth firing: unfired children, plus fired veins with pages left.

    One pool, deliberately. Deepening a proven vein and opening a fresh one are not two
    policies needing an alternation rule — they are two rows scored the same way, and the
    draw decides. A vein that stops paying accumulates ``b`` and sinks on its own.
    """
    from openoutreach.core.models import QueryNode

    return list(
        QueryNode.objects
        .filter(campaign=campaign,
                state__in=(QueryNode.State.FRONTIER, QueryNode.State.FIRED))
        .prefetch_related("keywords").select_related("parent")
    )


def next_node(campaign, store: LabelStore, rng=None):
    """The node to fire next, or ``None`` when the frontier is empty.

    ``None`` is the saturation signal: nothing left unfired and nothing left to deepen,
    which ``discover`` answers by refreshing the vocabulary and expanding again.
    """
    candidates = frontier(campaign)
    if not candidates:
        return None

    rng = np.random.default_rng() if rng is None else rng
    cache: dict = {}
    scored = []
    for node in candidates:
        alpha, beta = _beta_params(node, store, cache)
        score = rng.beta(alpha, beta) if THOMPSON else alpha / (alpha + beta)
        scored.append((score, node))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    score, best = scored[0]

    if logger.isEnabledFor(logging.DEBUG):
        # Why *this* query: the whole decision is these numbers, so print them rather
        # than the conclusion. `a`/`b` are the node's own counts, `lvl` its inherited
        # level, `P̂` the smoothed estimate and `draw` what Thompson actually sampled —
        # a node winning on a low P̂ means its posterior was wide, which is the point.
        logger.debug("[%s] frontier: %d node(s), base rate %.3f over %d label(s)",
                     campaign, len(candidates), store.base_rate, len(store))
        for rank, (drawn, node) in enumerate(scored[:_DEBUG_FRONTIER_ROWS], start=1):
            a, b = store.counts(node.pairs)
            level = estimate(node.parent, store, cache) if node.parent_id else store.base_rate
            marker = "→" if node.pk == best.pk else " "
            logger.debug("  %s #%d draw=%.3f P̂=%.3f  a=%d b=%d lvl=%.3f  %s@%d %s",
                         marker, rank, drawn, estimate(node, store, cache), a, b, level,
                         describe_filters(filters_for(node.pairs)),
                         node.next_offset, node.state)
        if len(scored) > _DEBUG_FRONTIER_ROWS:
            logger.debug("  … %d more", len(scored) - _DEBUG_FRONTIER_ROWS)
    return best


# ── node lifecycle ───────────────────────────────────────────────────

def _dead_sets(campaign) -> list[frozenset]:
    """Keyword sets already proven to match nobody — the anti-monotone prune.

    A superset of an empty conjunction is empty, so a dead node convicts every node that
    contains it. Checking at *creation* rather than storing a separate blacklist is what
    lets the prune reach supersets the walk would otherwise reach through a different
    parent — dedup on ``token_key`` makes the lattice a DAG, not a tree, so following
    parent links alone would miss them.
    """
    from openoutreach.core.models import QueryNode

    return [
        frozenset(node.pairs) for node in
        QueryNode.objects.filter(campaign=campaign, state=QueryNode.State.DEAD)
        .prefetch_related("keywords")
    ]


def seed_frontier(campaign, keywords) -> int:
    """Open the walk with one depth-1 node per keyword. Returns nodes created.

    There is no root. The empty query is never fired — it matches everyone, so its one
    10k window is the provider's famous-company head and tells us nothing — and the level
    a root would have supplied comes from ``LabelStore.base_rate`` instead.
    """
    created = 0
    for pair in keywords:
        if _upsert(campaign, [pair], parent=None) is not None:
            created += 1
    return created


def _upsert(campaign, pairs, parent, store: LabelStore | None = None, cache: dict | None = None):
    """Create a node, or re-point an existing one at a better parent. ``None`` if pruned.

    A node reachable by several paths keeps the parent giving the **highest** estimate.
    Optimism, and it matches how the level is used: the parent is a claim about the region
    this node sits in, and the best-supported claim is the one worth carrying.
    """
    from openoutreach.core.models import Keyword, QueryNode

    key = token_key(pairs)
    node = QueryNode.objects.filter(campaign=campaign, token_key=key).first()
    if node is not None:
        if (parent is not None and node.parent_id != parent.pk
                and node.state == QueryNode.State.FRONTIER and store is not None):
            if estimate(parent, store, cache) > estimate(node.parent, store, cache):
                node.parent = parent
                node.save(update_fields=["parent"])
        return None

    node = QueryNode.objects.create(campaign=campaign, token_key=key, parent=parent)
    node.keywords.set(Keyword.rows_for(pairs))
    return node


def expand(node, store: LabelStore, candidates) -> int:
    """Grow the frontier with this node's children. Returns how many were created.

    A child is this node plus one token that has shared a qualified profile with it
    (``LabelStore.cooccurring``), that no dead node is a subset of, and that does not push
    any one field past ``MAX_TOKENS_PER_FIELD``.
    """
    campaign = node.campaign
    pairs = node.pairs
    dead = _dead_sets(campaign)
    cache: dict = {}

    per_field = Counter(field for field, _ in pairs)
    offered = store.cooccurring(pairs, candidates)
    created = pruned = capped = 0
    for pair in offered:
        if per_field[pair[0]] >= MAX_TOKENS_PER_FIELD:
            capped += 1
            continue
        child_pairs = sorted([*pairs, pair])
        child_set = frozenset(child_pairs)
        if any(empty <= child_set for empty in dead):
            pruned += 1
            continue
        if _upsert(campaign, child_pairs, parent=node, store=store, cache=cache) is not None:
            created += 1

    if logger.isEnabledFor(logging.DEBUG):
        # "+0 nodes" is the frontier's most confusing outcome, and it has three very
        # different causes: no vocabulary to offer, no *qualified* profile pairing any
        # of it with this node, or every child already known. Name which one.
        logger.debug("  expand [%s]: %d vocabulary → %d co-occurring → "
                     "%d new, %d dead-pruned, %d field-capped, %d already known",
                     describe_filters(filters_for(pairs)), len(candidates), len(offered),
                     created, pruned, capped,
                     len(offered) - created - pruned - capped)
        if candidates and not offered:
            logger.debug("    nothing co-occurs: the store holds %d qualified profile(s), "
                         "and expansion only offers tokens seen alongside this node in one",
                         store.qualified_count)
    return created


def advance(node, leads_found: int | None = None) -> None:
    """Record a productive page: move the offset on, keep the node fireable.

    ``leads_found`` is the provider's corpus count, stored when offset 0 reported one.
    Diagnostic only — the walk fires nodes rather than counting them first — but it is the
    number that tells an operator whether a vein is 400 rows or 40 million.
    """
    from openoutreach.core.models import QueryNode

    node.next_offset += DISCOVERY_PAGE_SIZE
    node.state = QueryNode.State.FIRED
    fields = ["next_offset", "state"]
    if leads_found is not None:
        node.leads_found = leads_found
        fields.append("leads_found")
    node.save(update_fields=fields)


def retire(node, *, at_offset: int) -> str:
    """Retire an empty node and prune what its emptiness convicts. Returns what happened.

    Three cases, and they are not interchangeable — the provider reports ``0`` for all
    three (§7):

    - **offset 0** — the index matches nobody. The node is ``dead``, and so is every node
      containing its tokens: a superset matches a subset of people.
    - **past the end, below the reach cap** — the node drained completely, so every one of
      its people is already a ``Lead`` here. Its supersets are drawn from that same
      exhausted population, so they are pruned too; there is nothing new down there.
    - **past the end, at the reach cap** — we hit Elasticsearch's 10,000-row window, not
      the end of the population. The node retires but its **subtree stays**: adding a
      token opens a fresh 10k window over the part we could not reach.
    """
    from openoutreach.core.models import QueryNode

    if at_offset == 0:
        node.state = QueryNode.State.DEAD
        node.save(update_fields=["state"])
        _prune_descendants(node)
        return "dead"

    node.state = QueryNode.State.DRAINED
    node.save(update_fields=["state"])
    if at_offset < REACH_CAP:
        _prune_descendants(node)
        return "drained"
    return "capped"


def _prune_descendants(node) -> int:
    """Mark every node below this one dead. Returns how many.

    Walks the ``parent`` links breadth-first. Supersets reached through a *different*
    parent are handled at creation time instead (``_dead_sets``), which is the half of the
    prune that survives the lattice being a DAG.
    """
    from openoutreach.core.models import QueryNode

    pruned, generation = 0, [node.pk]
    while generation:
        children = list(
            QueryNode.objects.filter(campaign=node.campaign, parent_id__in=generation)
            .exclude(state=QueryNode.State.DEAD).values_list("pk", flat=True)
        )
        if not children:
            break
        QueryNode.objects.filter(pk__in=children).update(state=QueryNode.State.DEAD)
        pruned += len(children)
        generation = children
    return pruned
