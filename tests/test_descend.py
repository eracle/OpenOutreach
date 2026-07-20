# tests/test_descend.py
"""The lattice visit: deepest-first, widening only below an empty query.

``descend`` makes no provider call — it is a lookup over the clause pool, the fetched
nodes, and the blacklist. These tests drive it as the walk does (take a candidate,
mark it fetched, ask again) and assert on the sequence, because the order and the
empty-gated widening are the whole design.
"""
from openoutreach.core.models import Campaign, Clause, DiscoveryQuery, EmptyClauseSet
from openoutreach.core.pipeline import descend as descend_mod
from openoutreach.core.pipeline.descend import descend
from openoutreach.core.pipeline.frontier import clause_key


# ── helpers ──────────────────────────────────────────────────────────

def _campaign(pool=(), **kw):
    defaults = dict(name="C", product_docs="widgets", campaign_target="demos")
    defaults.update(kw)
    c = Campaign.objects.create(**defaults)
    c.clauses.set(Clause.rows_for(pool))
    return c


def _fetched(c, clauses):
    """A conjunction fetched with rows — a live query, so the visit won't widen it."""
    node = DiscoveryQuery.objects.create(campaign=c, clause_key=clause_key(clauses), offset=0)
    node.clauses.set(Clause.rows_for(clauses))
    return node


def _blacklist(clauses):
    """Record a conjunction as empty — what ``discover`` does on an offset-0 miss."""
    entry = EmptyClauseSet.objects.create(clause_key=clause_key(sorted(clauses)))
    entry.clauses.set(Clause.rows_for(clauses))


def _empty(c, clauses):
    """A conjunction fetched *and* empty — both the node and the blacklist entry."""
    _fetched(c, clauses)
    _blacklist(clauses)


def _walk(c, limit=32):
    """Every conjunction the visit yields, fetching each (with rows) as it goes."""
    visited = []
    for _ in range(limit):
        candidate = descend(c)
        if not candidate:
            return visited
        visited.append(candidate)
        _fetched(c, candidate)
    raise AssertionError("the visit never exhausted")


# A two-family pool, each family with two values: the maximal conjunctions are the
# four pairs, ordered by distance from the seed (each family's first value).
_POOL = [
    ("lead_job_title", "Founder"), ("lead_job_title", "CTO"),
    ("lead_location", "United States"), ("lead_location", "Germany"),
]
_SEED = [("lead_job_title", "Founder"), ("lead_location", "United States")]
_MAXIMAL = [
    _SEED,
    [("lead_job_title", "Founder"), ("lead_location", "Germany")],
    [("lead_job_title", "CTO"), ("lead_location", "United States")],
    [("lead_job_title", "CTO"), ("lead_location", "Germany")],
]

# A one-value-per-family pool: a single maximal conjunction, the triple.
_POOL3 = [
    ("lead_job_title", "Founder"),
    ("lead_location", "United States"),
    ("lead_seniority", "founder"),
]
_TRIPLE = sorted(_POOL3)


# ── deepest-only ─────────────────────────────────────────────────────

class TestDeepestOnly:
    def test_opens_on_the_seed_conjunction(self, db):
        assert descend(_campaign(_POOL)) == _SEED

    def test_visits_only_the_maximal_conjunctions_by_seed_distance(self, db):
        """One value per family, all families — never a shorter widening while they hold people."""
        visited = _walk(_campaign(_POOL))
        assert visited == _MAXIMAL
        assert all(len(v) == 2 for v in visited)

    def test_a_one_value_pool_visits_just_the_seed(self, db):
        assert _walk(_campaign(_POOL3)) == [_TRIPLE]

    def test_a_live_maximal_never_widens(self, db):
        """A query that returns rows is a dead end for the visit; only emptiness widens."""
        c = _campaign(_POOL3)
        _fetched(c, _TRIPLE)
        assert descend(c) == []

    def test_never_proposes_an_or(self, db):
        for candidate in _walk(_campaign(_POOL)):
            families = [family for family, _ in candidate]
            assert len(families) == len(set(families))


# ── widening below an empty query ────────────────────────────────────

class TestEmptyWidening:
    def test_an_empty_maximal_unlocks_its_drop_one_children(self, db):
        """`{a,b,c}` empty → visit `{a,b}`, `{a,c}`, `{b,c}` and nothing shallower."""
        c = _campaign(_POOL3)
        _empty(c, _TRIPLE)
        children = _walk(c)
        assert sorted(len(v) for v in children) == [2, 2, 2]
        a, b, cc = _TRIPLE
        assert {clause_key(v) for v in children} == {
            clause_key([a, b]), clause_key([a, cc]), clause_key([b, cc]),
        }

    def test_widening_recurses_through_empties(self, db):
        """An empty child widens again: below empty `{a,b}` come `{a}` and `{b}`."""
        c = _campaign(_POOL3)
        a, b, cc = _TRIPLE
        _empty(c, _TRIPLE)
        _empty(c, [a, b])
        keys = {clause_key(v) for v in _walk(c)}
        assert clause_key([a, cc]) in keys and clause_key([b, cc]) in keys  # live pairs
        assert clause_key([a]) in keys and clause_key([b]) in keys          # below empty {a,b}
        assert clause_key([a, b]) not in keys                               # empty, never re-fetched

    def test_a_dead_singleton_reroutes_the_descent_around_it(self, db):
        """A known-empty singleton kills every conjunction holding it; widening below
        those drops the dead clause, so the descent routes around it."""
        c = _campaign(_POOL)
        _blacklist([("lead_location", "Germany")])
        assert not any(("lead_location", "Germany") in v for v in _walk(c))


# ── pruning (the subset test) ────────────────────────────────────────

class TestPruning:
    def test_prunes_a_superset_of_a_known_empty_set(self, db):
        _blacklist([("lead_job_title", "CTO"), ("lead_location", "Germany")])
        candidate = frozenset([
            ("lead_job_title", "CTO"), ("lead_location", "Germany"),
            ("lead_seniority", "founder"),
        ])
        assert descend_mod._is_pruned(candidate, descend_mod._empty_sets())

    def test_keeps_a_candidate_that_merely_overlaps_a_known_empty_set(self, db):
        """Emptiness convicts the set, never a clause inside it — `Sales` returns rows
        alone and must survive the conjunctions it happened to sit in."""
        _blacklist([("lead_department", "Sales"), ("lead_location", "Japan")])
        candidate = frozenset([("lead_department", "Sales"), ("lead_location", "Germany")])
        assert not descend_mod._is_pruned(candidate, descend_mod._empty_sets())


# ── exhaustion ───────────────────────────────────────────────────────

class TestExhaustion:
    def test_returns_the_next_unfetched_maximal(self, db):
        c = _campaign(_POOL)
        _fetched(c, _SEED)
        assert descend(c) == [("lead_job_title", "Founder"), ("lead_location", "Germany")]

    def test_returns_empty_when_every_maximal_is_live(self, db):
        """The one honest 'the pool is used up' — what licenses the LLM refill."""
        c = _campaign(_POOL)
        _walk(c)
        assert descend(c) == []

    def test_an_empty_pool_asks_for_nothing(self, db):
        assert descend(_campaign([])) == []
