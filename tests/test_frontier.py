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
        case: the visit order reaches level 1 before any conjunction anyway."""
        c = _campaign()

        def _seed(campaign):
            _pool(campaign, SEED)
            return SEED

        with patch("openoutreach.core.pipeline.icp.generate_seed", side_effect=_seed) as gen:
            q = frontier.next_query(c)

        gen.assert_called_once()
        assert q == frontier.NextQuery([("lead_job_title", "Founder")], 0, "visit")
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
        """``k=1`` is the case ``Clause.is_live`` used to own, and the reason the visit
        opens at level 1: only sets shorter than a candidate can prune it."""
        from openoutreach.core.models import EmptyClauseSet

        frontier.record_empty([("lead_location", "Europe")])
        assert EmptyClauseSet.objects.get().clauses.count() == 1


class TestDeepen:
    def test_deepens_the_node_with_most_qualified(self, db):
        """The winner is the productive node whose next page is still open, and it is
        paged by *its own* offset — not the line's high-water mark."""
        c = _campaign()
        cold = _node(c, SEED, offset=0)
        _examined(c, cold, "a1")
        _rejected(c, cold, "a2")            # 2 examined, 1 qualified — page 100 open
        hot = _node(c, OTHER, offset=0)
        for tag in ("b1", "b2", "b3"):
            _examined(c, hot, tag)          # 3 examined, 3 qualified — but page 100…
        _node(c, OTHER, offset=100)      # …already fetched, so hot cannot re-vote
        q = frontier.next_query(c)
        assert q == frontier.NextQuery(SEED, 100, "deepen")

    def test_a_shallow_page_cannot_re_elect_a_line_it_already_paged(self, db):
        """The bug being fixed: an offset-0 page that qualified must not keep voting a
        line deeper. Once its own next page exists, it is spent — only a page whose
        successor is still unfetched deepens, so a barren-but-examined frontier ends
        the descent instead of the shallow page driving the offset up forever."""
        c = _campaign()
        _pool(c, SEED)
        root = _node(c, OTHER, offset=0)
        for tag in ("b1", "b2", "b3", "b4", "b5"):
            _examined(c, root, tag)          # 5 qualified at offset 0 — kept forever
        frontier_pg = _node(c, OTHER, offset=100)
        _rejected(c, frontier_pg, "c1")      # examined, nothing qualified → dead end
        with patch("openoutreach.core.pipeline.mutate.generate_mutation",
                   return_value=SEED):
            q = frontier.next_query(c)
        # not NextQuery(OTHER, 200, ...): the frontier page didn't pay, and offset 0
        # cannot re-elect the line just because it once did.
        assert q == frontier.NextQuery(SEED, 0, "visit")

    def test_a_paying_frontier_page_earns_the_next_one(self, db):
        """The complement: when the deepest page itself qualifies, deepen continues —
        from that page's offset, one page on."""
        c = _campaign()
        _node(c, OTHER, offset=0)
        frontier_pg = _node(c, OTHER, offset=100)
        _examined(c, frontier_pg, "c1")      # the frontier page paid
        q = frontier.next_query(c)
        assert q == frontier.NextQuery(OTHER, 200, "deepen")

    def test_skips_exhausted_productive_node(self, db):
        c = _campaign()
        alive = _node(c, SEED, offset=0)
        _examined(c, alive, "a1")
        dry = _node(c, OTHER, offset=0, exhausted=True)  # richer, but dried up
        for tag in ("b1", "b2", "b3"):
            _examined(c, dry, tag)
        q = frontier.next_query(c)
        assert q.clauses == SEED and q.move == "deepen"

    def test_a_seed_that_pays_is_deepened_not_walled_away_from(self, db):
        """The bug this whole change exists to kill.

        The old score asked the GP how many of a node's leads cleared 0.9 — none
        ever did, so a seed with real qualified leads scored 0, read as a wall, and
        got mutated away from. ``deepen`` never fired on any node, ever.
        """
        c = _campaign()
        seed = _node(c, SEED, offset=0)
        _examined(c, seed, "won")
        for tag in ("lost1", "lost2", "lost3"):
            _rejected(c, seed, tag)  # a thin 1-in-4 vein is still a vein
        with patch("openoutreach.core.pipeline.mutate.generate_mutation",
                   side_effect=AssertionError("a paying node must be deepened, not abandoned")):
            q = frontier.next_query(c)
        assert q == frontier.NextQuery(SEED, 100, "deepen")


