# tests/test_descend.py
"""The wall move as a lattice lookup — the singleton sweep, anti-monotone pruning,
and the descent that composes conjunctions from the campaign's clause pool.

The provider is mocked at its boundary (``discovery.probe``), and every test
asserts on **which conjunctions were probed**, not just on what came back: the
whole value of this module is the calls it *doesn't* make, and a test that only
checks the return value cannot tell a pruned lattice from an exhaustively walked
one."""
from unittest.mock import patch

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
    """A fetched node — what makes a conjunction 'already tried'."""
    node = DiscoveryQuery.objects.create(
        campaign=c, clause_key=clause_key(clauses), offset=offset,
    )
    node.clauses.set(Clause.rows_for(clauses))
    return node


def _probe_stub(dead=()):
    """A fake ``probe`` matching nothing whose clause set contains a dead pair.

    Returns ``(stub, probed)`` — ``probed`` accumulates each conjunction the
    descent actually spent a call on, which is what the pruning tests assert.
    """
    dead, probed = {tuple(d) for d in dead}, []

    def stub(clauses):
        clauses = sorted(clauses)
        probed.append(clauses)
        return not any(pair in dead for pair in clauses)

    return stub, probed


# A two-family pool spanning four conjunctions, ordered by the ICP's own ranking:
# Founder/US is the seed (each family's first value), then one hop out, then two.
_POOL = [
    ("lead_job_title", "Founder"), ("lead_job_title", "CTO"),
    ("lead_location", "United States"), ("lead_location", "Germany"),
]
_SEED = [("lead_job_title", "Founder"), ("lead_location", "United States")]


# ── the singleton sweep ──────────────────────────────────────────────

class TestSingletonSweep:
    def test_probes_each_pool_clause_alone_and_records_the_verdict(self, db):
        c = _campaign(_POOL)
        stub, probed = _probe_stub(dead=[("lead_location", "Germany")])
        with patch.object(descend_mod, "probe", stub):
            descend(c)

        assert [len(p) for p in probed[:4]] == [1, 1, 1, 1], "the sweep probes singletons"
        assert Clause.objects.get(family="lead_location", value="Germany").is_live is False
        assert Clause.objects.get(family="lead_job_title", value="Founder").is_live is True

    def test_a_dead_clause_prunes_every_conjunction_holding_it(self, db):
        """The anti-monotone payoff: `Europe` dies once, not once per query."""
        c = _campaign(_POOL)
        stub, probed = _probe_stub(dead=[("lead_location", "Germany")])
        with patch.object(descend_mod, "probe", stub):
            descend(c)

        conjunctions = [p for p in probed if len(p) > 1]
        assert conjunctions, "the descent got past the sweep"
        assert not any(("lead_location", "Germany") in p for p in conjunctions), (
            "a clause the sweep retired must never be probed again inside a conjunction"
        )

    def test_a_clause_is_swept_once_for_all_time(self, db):
        c = _campaign(_POOL)
        stub, _ = _probe_stub()
        with patch.object(descend_mod, "probe", stub):
            descend(c)
        stub2, probed2 = _probe_stub()
        with patch.object(descend_mod, "probe", stub2):
            descend(c)

        assert not [p for p in probed2 if len(p) == 1], (
            "the sweep re-probed a clause it already has a verdict on"
        )

    def test_a_family_wiped_out_by_the_sweep_drops_out_of_the_conjunction(self, db):
        """Candidates stay as deep as the *surviving* pool allows, not deeper."""
        c = _campaign(_POOL)
        stub, _ = _probe_stub(dead=[("lead_location", "United States"),
                                    ("lead_location", "Germany")])
        with patch.object(descend_mod, "probe", stub):
            result = descend(c)

        assert result == [("lead_job_title", "Founder")]


# ── the descent ──────────────────────────────────────────────────────

class TestDescend:
    def test_composes_the_deepest_conjunction_the_pool_spans(self, db):
        c = _campaign(_POOL)
        stub, _ = _probe_stub()
        with patch.object(descend_mod, "probe", stub):
            result = descend(c)

        assert result == _SEED, "one value per family, every family present"

    def test_returns_the_next_untried_conjunction(self, db):
        """The seed is already fetched, so the descent must hop, not re-propose it."""
        c = _campaign(_POOL)
        _node(c, _SEED)
        stub, _ = _probe_stub()
        with patch.object(descend_mod, "probe", stub):
            result = descend(c)

        assert result != _SEED
        assert result in (
            [("lead_job_title", "CTO"), ("lead_location", "United States")],
            [("lead_job_title", "Founder"), ("lead_location", "Germany")],
        )

    def test_never_proposes_an_or(self, db):
        """One clause per family is what makes an OR unrepresentable at this seam."""
        c = _campaign(_POOL)
        stub, _ = _probe_stub()
        with patch.object(descend_mod, "probe", stub):
            result = descend(c)

        families = [family for family, _ in result]
        assert len(families) == len(set(families))

    def test_an_empty_conjunction_is_blacklisted_and_skipped_on_the_next_move(self, db):
        c = _campaign(_POOL)
        # Every conjunction is empty; only the singletons live. The descent must
        # record each dead set rather than re-probing it forever.
        def stub(clauses):
            return len(sorted(clauses)) == 1

        with patch.object(descend_mod, "probe", stub):
            assert descend(c) == []
        assert EmptyClauseSet.objects.count() == 4

        stub2, probed2 = _probe_stub()
        with patch.object(descend_mod, "probe", stub2):
            assert descend(c) == []
        assert probed2 == [], "a conjunction already known empty was probed again"

    def test_returns_empty_when_every_conjunction_is_fetched(self, db):
        """The one honest 'the pool is used up' — what licenses the LLM refill."""
        c = _campaign(_POOL)
        for title in ("Founder", "CTO"):
            for loc in ("United States", "Germany"):
                _node(c, sorted([("lead_job_title", title), ("lead_location", loc)]))
        stub, probed = _probe_stub()
        with patch.object(descend_mod, "probe", stub):
            assert descend(c) == []
        assert not [p for p in probed if len(p) > 1], "a fetched conjunction was re-probed"

    def test_an_empty_pool_asks_for_nothing(self, db):
        c = _campaign([])
        stub, probed = _probe_stub()
        with patch.object(descend_mod, "probe", stub):
            assert descend(c) == []
        assert probed == []


class TestPruning:
    def test_prunes_a_superset_of_a_known_empty_set(self, db):
        empty = [("lead_job_title", "CTO"), ("lead_location", "Germany")]
        entry = EmptyClauseSet.objects.create(clause_key=clause_key(empty))
        entry.clauses.set(Clause.rows_for(empty))

        candidate = frozenset(empty + [("lead_seniority", "founder")])
        assert descend_mod._is_pruned(candidate, descend_mod._empty_sets())

    def test_keeps_a_candidate_that_merely_overlaps_a_known_empty_set(self, db):
        """Emptiness convicts the set, never a clause inside it — `Sales` returns
        rows alone and must survive the conjunctions it happened to sit in."""
        empty = [("lead_department", "Sales"), ("lead_location", "Japan")]
        entry = EmptyClauseSet.objects.create(clause_key=clause_key(empty))
        entry.clauses.set(Clause.rows_for(empty))

        candidate = frozenset([("lead_department", "Sales"), ("lead_location", "Germany")])
        assert not descend_mod._is_pruned(candidate, descend_mod._empty_sets())
