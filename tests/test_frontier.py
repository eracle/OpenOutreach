# tests/test_frontier.py
"""Discovery frontier — the walk over DiscoveryQuery nodes: clause-set identity +
dedup, the ground-truth node metric, the deepen/visit selector, node persistence,
and reactive exhaustion.

No qualifier appears anywhere in this file, and that is the point: the walk is
steered by counted deals, not by a GP prediction. If a stub ever needs to come
back, something has started reading the model again."""
from unittest.mock import patch

from openoutreach.core.models import Campaign, Clause, DiscoveryQuery
from openoutreach.core.pipeline import frontier
from openoutreach.crm.models import Deal, DealState, Lead, Outcome


# ── helpers ──────────────────────────────────────────────────────────

def _campaign(**kw):
    defaults = dict(name="C", product_docs="widgets", campaign_target="demos")
    defaults.update(kw)
    return Campaign.objects.create(**defaults)


def _node(campaign, clauses, offset=0, exhausted=False):
    node = DiscoveryQuery.objects.create(
        campaign=campaign, clause_key=frontier.clause_key(clauses),
        offset=offset, exhausted=exhausted,
    )
    node.clauses.set(Clause.rows_for(clauses))
    return node


# Two clause sets, in the shape the walk composes them: one clause per family.
SEED = [("lead_job_title", "Founder"), ("lead_location", "United States")]
OTHER = [("lead_job_title", "CTO"), ("lead_location", "Japan")]


def _lead(node, tag):
    """A first-touch lead for ``node``."""
    return Lead.objects.create(
        profile_url=f"https://x/{node.pk}-{tag}/", discovered_by=node,
    )


def _examined(campaign, node, tag, *, state=DealState.QUALIFIED, outcome=""):
    """A lead of ``node`` the LLM has ruled on — i.e. one carrying a Deal."""
    return Deal.objects.create(
        lead=_lead(node, tag), campaign=campaign, state=state, outcome=outcome,
    )


def _rejected(campaign, node, tag):
    """An LLM rejection: FAILED + wrong_fit. Examined, not qualified."""
    return _examined(campaign, node, tag,
                     state=DealState.FAILED, outcome=Outcome.WRONG_FIT)


# ── clause-set identity ──────────────────────────────────────────────

class TestClauseIdentity:
    def test_key_is_order_independent(self):
        a = [("lead_seniority", "vp"), ("company_headcount_min", "1")]
        b = [("company_headcount_min", "1"), ("lead_seniority", "vp")]
        assert frontier.clause_key(a) == frontier.clause_key(b)

    def test_key_differs_on_value(self):
        assert (frontier.clause_key([("lead_location", "Japan")])
                != frontier.clause_key([("lead_location", "Germany")]))

    def test_key_differs_on_depth(self):
        """A conjunction is not its own sub-conjunction — it samples a different
        window, so it must be a different node."""
        shallow = [("lead_location", "Japan")]
        deep = [("lead_location", "Japan"), ("lead_job_title", "CTO")]
        assert frontier.clause_key(shallow) != frontier.clause_key(deep)


# ── persist / exhaust ────────────────────────────────────────────────

class TestPersistAndExhaust:
    def test_persist_is_deduped_on_triple(self, db):
        c = _campaign()
        a = frontier.persist_fetched(c, SEED, offset=0)
        b = frontier.persist_fetched(c, SEED, offset=0)  # exact twin
        assert a.pk == b.pk
        assert DiscoveryQuery.objects.filter(campaign=c).count() == 1
        # a deeper page of the same clause set is a distinct node
        frontier.persist_fetched(c, SEED, offset=100)
        assert DiscoveryQuery.objects.filter(campaign=c).count() == 2

    def test_persist_leaves_node_active(self, db):
        c = _campaign()
        node = frontier.persist_fetched(c, SEED, offset=0)
        assert node.exhausted is False

    def test_mark_exhausted_flags_whole_line(self, db):
        c = _campaign()
        p0 = _node(c, SEED, offset=0)
        p1 = _node(c, SEED, offset=100)
        other = _node(c, OTHER, offset=0)
        frontier.mark_exhausted(c, SEED)
        p0.refresh_from_db(); p1.refresh_from_db(); other.refresh_from_db()
        assert p0.exhausted and p1.exhausted  # every offset of the line
        assert not other.exhausted             # a different query is untouched


# ── readability ──────────────────────────────────────────────────────

class TestNodeRendering:
    def test_str_names_the_region_not_the_row(self, db):
        c = _campaign()
        node = _node(c, [("company_headcount_min", "1"), ("company_headcount_max", "20"),
                         ("lead_job_title", "Founder")], offset=100)
        # A node IS its clause set; "DiscoveryQuery#10" tells a reader nothing about
        # what was searched, and these render in logs and admin.
        assert str(node) == "headcount 1–20 · job_title Founder @100"

    def test_str_flags_an_exhausted_line(self, db):
        c = _campaign()
        node = _node(c, [("lead_location", "Japan")], exhausted=True)
        assert str(node) == "location Japan @0 (exhausted)"


# ── the node metric ──────────────────────────────────────────────────

class TestNodeStats:
    def test_counts_examined_and_qualified(self, db):
        c = _campaign()
        node = _node(c, SEED)
        _examined(c, node, "a")
        _examined(c, node, "b")
        _rejected(c, node, "c")
        assert frontier.node_stats(c)[node.pk] == frontier.NodeStats(3, 2)

    def test_unexamined_node_is_absent_not_zero(self, db):
        c = _campaign()
        node = _node(c, SEED)
        _lead(node, "never-ruled-on")  # discovered, but no Deal
        # Absent, not NodeStats(0, 0) — "nobody looked" must not read as "barren".
        assert node.pk not in frontier.node_stats(c)

    def test_qualified_survives_the_lead_advancing(self, db):
        """A node's value must not fall as its leads succeed down the funnel.

        Counting ``state == QUALIFIED`` would do exactly that: the deal moves on to
        READY_TO_EMAIL and the vein would look barren the moment it started paying.
        """
        c = _campaign()
        node = _node(c, SEED)
        _examined(c, node, "a", state=DealState.EMAILED)
        _examined(c, node, "b", state=DealState.COMPLETED, outcome=Outcome.CONVERTED)
        assert frontier.node_stats(c)[node.pk] == frontier.NodeStats(2, 2)

    def test_operational_failure_still_counts_as_qualified(self, db):
        """FAILED with a blank outcome is the "no email" miss — the LLM said yes."""
        c = _campaign()
        node = _node(c, SEED)
        _examined(c, node, "a", state=DealState.FAILED, outcome="")
        assert frontier.node_stats(c)[node.pk] == frontier.NodeStats(1, 1)

    def test_is_scoped_to_the_campaign(self, db):
        c, other = _campaign(), _campaign(name="D")
        node = _node(c, SEED)
        _examined(other, node, "a")  # same node, another campaign's deal
        assert node.pk not in frontier.node_stats(c)


# ── selection: deepen / visit ────────────────────────────────────────

def _pool(c, clauses):
    """Give the campaign a clause pool — the precondition for composing anything."""
    c.clauses.set(Clause.rows_for(clauses))


class TestColdStart:
    def test_seeds_the_pool_when_it_is_empty(self, db):
        """The ICP's job is the pool. The seed conjunction it returns needs no special
        case: deepest-first makes it the head of the visit anyway."""
        c = _campaign()

        def _seed(campaign):
            _pool(campaign, SEED)
            return SEED

        with patch("openoutreach.core.pipeline.icp.generate_seed", side_effect=_seed) as gen:
            q = frontier.next_query(c)

        gen.assert_called_once()
        assert q == frontier.NextQuery(SEED, 0, "visit")
        # nothing is cached — the first fetched page becomes the node
        assert not DiscoveryQuery.objects.filter(campaign=c).exists()

    def test_does_not_reseed_when_the_pool_exists(self, db):
        c = _campaign()
        _pool(c, SEED)
        with patch("openoutreach.core.pipeline.icp.generate_seed",
                   side_effect=AssertionError("the pool exists — must not reseed")):
            assert frontier.next_query(c).move == "visit"

    def test_none_when_icp_empty(self, db):
        c = _campaign()
        with patch("openoutreach.core.pipeline.icp.generate_seed", return_value=[]), \
             patch("openoutreach.core.pipeline.mutate.generate_mutation", return_value=[]):
            assert frontier.next_query(c) is None


class TestVisit:
    def test_a_barren_node_is_left_alone_and_the_walk_carries_on(self, db):
        c = _campaign()
        _pool(c, SEED)
        _rejected(c, _node(c, SEED, offset=0), "a1")
        with patch("openoutreach.core.pipeline.mutate.generate_mutation",
                   return_value=OTHER) as gen:
            q = frontier.next_query(c)
        gen.assert_called_once()
        assert q == frontier.NextQuery(OTHER, 0, "visit")

    def test_an_unexamined_node_is_not_deepened(self, db):
        """Leads nobody has ruled on are not evidence. Deepen needs a *qualified*
        lead, so an unexamined node cannot earn a page — nor be convicted for it."""
        c = _campaign()
        _pool(c, SEED)
        fresh = _node(c, OTHER, offset=0)
        _lead(fresh, "unruled")
        with patch("openoutreach.core.pipeline.mutate.generate_mutation", return_value=SEED):
            q = frontier.next_query(c)
        assert q == frontier.NextQuery(SEED, 0, "visit")

    def test_an_exhausted_line_does_not_stop_the_walk(self, db):
        """Emptiness retires a line, not the campaign — the lattice has more in it."""
        c = _campaign()
        _pool(c, SEED)
        _node(c, SEED, offset=0, exhausted=True)
        with patch("openoutreach.core.pipeline.mutate.generate_mutation",
                   return_value=OTHER):
            assert frontier.next_query(c) == frontier.NextQuery(OTHER, 0, "visit")

    def test_none_when_the_composer_is_dry(self, db):
        c = _campaign()
        _pool(c, SEED)
        _rejected(c, _node(c, SEED, offset=0), "a1")
        with patch("openoutreach.core.pipeline.mutate.generate_mutation", return_value={}):
            assert frontier.next_query(c) is None

    def test_none_when_the_composer_reproposes_a_fetched_query(self, db):
        c = _campaign()
        _pool(c, SEED)
        _rejected(c, _node(c, SEED, offset=0), "a1")
        with patch("openoutreach.core.pipeline.mutate.generate_mutation",
                   return_value=SEED):
            assert frontier.next_query(c) is None


class TestRecordEmpty:
    def test_blacklists_a_conjunction_idempotently(self, db):
        from openoutreach.core.models import EmptyClauseSet

        frontier.record_empty(OTHER)
        frontier.record_empty(OTHER)
        entry = EmptyClauseSet.objects.get()
        assert entry.clause_key == frontier.clause_key(OTHER)
        assert set(entry.clauses.values_list("family", "value")) == set(OTHER)

    def test_a_singleton_is_a_first_class_empty_set(self, db):
        """``k=1`` is the case ``Clause.is_live`` used to own: a singleton is a
        first-class empty set, and only sets shorter than a candidate can prune it."""
        from openoutreach.core.models import EmptyClauseSet

        frontier.record_empty([("lead_location", "Europe")])
        assert EmptyClauseSet.objects.get().clauses.count() == 1


class TestLineStats:
    def test_sums_qualified_across_a_lines_offsets(self, db):
        """A conjunction's value is its qualified count over ALL offsets, not per page."""
        c = _campaign()
        p0 = _node(c, SEED, offset=0)
        p1 = _node(c, SEED, offset=100)
        _examined(c, p0, "a")        # qualified at offset 0
        _examined(c, p1, "b")        # qualified at offset 100
        _rejected(c, p1, "c")
        assert frontier.line_stats(c)[frontier.clause_key(SEED)] == frontier.NodeStats(3, 2)


class TestProductiveLine:
    """Deepen *selection* — which line to page, independent of the visit alternation."""

    def test_deepens_the_line_with_most_qualified(self, db):
        """The winner is the conjunction with the most qualified leads, paged past its
        deepest offset — even when that deepest page itself qualified nobody."""
        c = _campaign()
        thin = _node(c, SEED, offset=0)
        _examined(c, thin, "a1")                 # SEED line: 1 qualified
        rich0 = _node(c, OTHER, offset=0)
        rich1 = _node(c, OTHER, offset=100)      # deepest page of the OTHER line…
        for tag in ("b1", "b2", "b3"):
            _examined(c, rich0, tag)             # …3 qualified, all at offset 0
        _rejected(c, rich1, "c")                 # offset 100 qualified nobody
        assert frontier._productive_line(c) == frontier.NextQuery(OTHER, 200, "deepen")

    def test_a_qualified_lead_at_offset_0_keeps_paging_the_line(self, db):
        """The behaviour the old per-node rule forbade: offset 0's qualified leads
        justify offset 100, 200 … for as long as the line has not emptied."""
        c = _campaign()
        p0 = _node(c, SEED, offset=0)
        _examined(c, p0, "a")            # qualified only at offset 0
        _node(c, SEED, offset=100)       # offset 100 fetched, qualified nobody
        assert frontier._productive_line(c) == frontier.NextQuery(SEED, 200, "deepen")

    def test_skips_an_exhausted_line(self, db):
        c = _campaign()
        alive = _node(c, SEED, offset=0)
        _examined(c, alive, "a1")
        dry = _node(c, OTHER, offset=0, exhausted=True)  # richer, but dried up
        for tag in ("b1", "b2", "b3"):
            _examined(c, dry, tag)
        line = frontier._productive_line(c)
        assert line.clauses == SEED and line.move == "deepen"

    def test_none_until_something_qualifies(self, db):
        c = _campaign()
        fresh = _node(c, SEED, offset=0)
        _lead(fresh, "unruled")          # discovered, never examined → no vote
        assert frontier._productive_line(c) is None


class TestInterleave:
    """The 1:1 alternation in ``next_query``, keyed on the newest node's offset."""

    def test_deepens_after_a_visit(self, db):
        """Last move opened a conjunction (offset 0) → this move deepens, no LLM call."""
        c = _campaign()
        _pool(c, SEED)
        seed0 = _node(c, SEED, offset=0)          # newest node: a visit
        _examined(c, seed0, "a")                  # productive line
        with patch("openoutreach.core.pipeline.mutate.generate_mutation",
                   side_effect=AssertionError("after a visit, deepen — don't compose")):
            q = frontier.next_query(c)
        assert q == frontier.NextQuery(SEED, 100, "deepen")

    def test_visits_after_a_deepen(self, db):
        """Last move deepened (offset > 0) → this move opens a new conjunction."""
        c = _campaign()
        _pool(c, SEED)
        seed0 = _node(c, SEED, offset=0)
        _examined(c, seed0, "a")
        _node(c, SEED, offset=100)                # newest node: a deepen
        with patch("openoutreach.core.pipeline.mutate.generate_mutation",
                   return_value=OTHER) as gen:
            q = frontier.next_query(c)
        gen.assert_called_once()
        assert q == frontier.NextQuery(OTHER, 0, "visit")

    def test_visits_when_nothing_has_qualified(self, db):
        """Deepen is unavailable in cold start, so every move is a visit."""
        c = _campaign()
        _pool(c, SEED)
        with patch("openoutreach.core.pipeline.mutate.generate_mutation", return_value=SEED):
            assert frontier.next_query(c) == frontier.NextQuery(SEED, 0, "visit")

    def test_falls_back_to_deepen_when_the_visit_is_dry(self, db):
        """After a deepen we'd visit, but the composer has nothing new — deepen anyway."""
        c = _campaign()
        _pool(c, SEED)
        seed0 = _node(c, SEED, offset=0)
        _examined(c, seed0, "a")
        _node(c, SEED, offset=100)                # newest node: a deepen → prefer visit
        with patch("openoutreach.core.pipeline.mutate.generate_mutation", return_value=[]):
            q = frontier.next_query(c)
        assert q == frontier.NextQuery(SEED, 200, "deepen")


