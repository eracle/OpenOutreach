# tests/test_descend.py
"""The lattice visit — its order, and the anti-monotone pruning that order exists for.

``descend`` makes **no provider call**: it is a pure lookup over the campaign's
clause pool, the nodes already fetched, and the blacklist. So these tests drive it
the way the walk does — take a candidate, mark it fetched, ask again — and assert on
the *sequence*, because the order is the whole design. A test that only checked one
return value could not tell a pruned lattice from an exhaustively walked one.
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


def _node(c, clauses, offset=0):
    """A fetched node — what makes a conjunction 'already visited'."""
    node = DiscoveryQuery.objects.create(
        campaign=c, clause_key=clause_key(clauses), offset=offset,
    )
    node.clauses.set(Clause.rows_for(clauses))
    return node


def _blacklist(clauses):
    """Record a conjunction as empty — what ``discover`` does on an offset-0 miss."""
    entry = EmptyClauseSet.objects.create(clause_key=clause_key(sorted(clauses)))
    entry.clauses.set(Clause.rows_for(clauses))
    return entry


def _walk(c, limit=32):
    """Every conjunction the visit yields, fetching each one as the walk would."""
    visited = []
    for _ in range(limit):
        candidate = descend(c)
        if not candidate:
            return visited
        visited.append(candidate)
        _node(c, candidate)
    raise AssertionError("the visit never exhausted")


# A two-family pool, ordered by the ICP's own ranking: Founder/US is the seed (each
# family's first value), then one hop out, then two.
_POOL = [
    ("lead_job_title", "Founder"), ("lead_job_title", "CTO"),
    ("lead_location", "United States"), ("lead_location", "Germany"),
]
_SEED = [("lead_job_title", "Founder"), ("lead_location", "United States")]

# A one-value-per-family pool, so the lattice has three distinct levels and the
# backtrack is visible: 3 singletons, 1 triple, 3 pairs.
_POOL3 = [
    ("lead_job_title", "Founder"),
    ("lead_location", "United States"),
    ("lead_seniority", "founder"),
]


# ── the visit order ──────────────────────────────────────────────────

class TestVisitOrder:
    def test_singletons_come_first(self, db):
        """Level 1 leads, because it is the only level whose emptiness prunes."""
        c = _campaign(_POOL)
        assert descend(c) == [("lead_job_title", "Founder")]

    def test_order_is_one_then_deepest_then_backtrack(self, db):
        c = _campaign(_POOL3)
        assert [len(v) for v in _walk(c)] == [1, 1, 1, 3, 2, 2, 2]

    def test_the_seed_is_the_head_of_its_level(self, db):
        """No special case for the seed anywhere — the ranking puts it there."""
        c = _campaign(_POOL)
        conjunctions = [v for v in _walk(c) if len(v) > 1]
        assert conjunctions[0] == _SEED

    def test_visits_every_conjunction_the_pool_spans_exactly_once(self, db):
        c = _campaign(_POOL)
        visited = _walk(c)
        assert len(visited) == 8, "4 singletons + 4 pairs"
        assert len({clause_key(v) for v in visited}) == 8

    def test_never_proposes_an_or(self, db):
        """One clause per family is what makes an OR unrepresentable at this seam."""
        c = _campaign(_POOL)
        for candidate in _walk(c):
            families = [family for family, _ in candidate]
            assert len(families) == len(set(families))


# ── pruning ──────────────────────────────────────────────────────────

class TestPruning:
    def test_a_dead_singleton_prunes_every_conjunction_holding_it(self, db):
        """The anti-monotone payoff, and why level 1 goes first: `Germany` dies once,
        not once per query that happens to mention it."""
        c = _campaign(_POOL)
        _blacklist([("lead_location", "Germany")])

        visited = _walk(c)
        assert not any(("lead_location", "Germany") in v for v in visited)
        assert len(visited) == 5, "3 surviving singletons + 2 pairs"

    def test_a_blacklisted_conjunction_is_never_revisited(self, db):
        c = _campaign(_POOL)
        _blacklist(_SEED)

        assert _SEED not in _walk(c)

    def test_prunes_a_superset_of_a_known_empty_set(self, db):
        empty = [("lead_job_title", "CTO"), ("lead_location", "Germany")]
        _blacklist(empty)

        candidate = frozenset(empty + [("lead_seniority", "founder")])
        assert descend_mod._is_pruned(candidate, descend_mod._empty_sets())

    def test_keeps_a_candidate_that_merely_overlaps_a_known_empty_set(self, db):
        """Emptiness convicts the set, never a clause inside it — `Sales` returns
        rows alone and must survive the conjunctions it happened to sit in."""
        _blacklist([("lead_department", "Sales"), ("lead_location", "Japan")])

        candidate = frozenset([("lead_department", "Sales"), ("lead_location", "Germany")])
        assert not descend_mod._is_pruned(candidate, descend_mod._empty_sets())


# ── exhaustion ───────────────────────────────────────────────────────

class TestExhaustion:
    def test_returns_the_next_unvisited_conjunction(self, db):
        """The seed is already fetched, so the visit must hop, not re-propose it."""
        c = _campaign(_POOL)
        for clauses in ([("lead_job_title", "Founder")], [("lead_job_title", "CTO")],
                        [("lead_location", "United States")], [("lead_location", "Germany")]):
            _node(c, clauses)
        _node(c, _SEED)

        assert descend(c) == [("lead_job_title", "CTO"), ("lead_location", "United States")]

    def test_returns_empty_when_every_conjunction_is_fetched(self, db):
        """The one honest 'the pool is used up' — what licenses the LLM refill."""
        c = _campaign(_POOL)
        _walk(c)
        assert descend(c) == []

    def test_an_empty_pool_asks_for_nothing(self, db):
        assert descend(_campaign([])) == []
