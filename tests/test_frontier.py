# tests/test_frontier.py
"""Discovery frontier — the lazy best-first walk over DiscoveryQuery nodes:
params identity + dedup, seeding, the ground-truth node metric, the
bootstrap/deepen/wall selector, node persistence, and reactive exhaustion.

No qualifier appears anywhere in this file, and that is the point: the walk is
steered by counted deals, not by a GP prediction. If a stub ever needs to come
back, something has started reading the model again."""
from unittest.mock import patch

from openoutreach.core.models import Campaign, DiscoveryQuery
from openoutreach.core.pipeline import frontier
from openoutreach.crm.models import Deal, DealState, Lead, Outcome


# ── helpers ──────────────────────────────────────────────────────────

def _campaign(**kw):
    defaults = dict(name="C", product_docs="widgets", campaign_target="demos")
    defaults.update(kw)
    return Campaign.objects.create(**defaults)


def _node(campaign, params, offset=0, exhausted=False):
    return DiscoveryQuery.objects.create(
        campaign=campaign, params=params, params_hash=frontier.params_hash(params),
        offset=offset, exhausted=exhausted,
    )


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


# ── params identity ──────────────────────────────────────────────────

class TestParamsIdentity:
    def test_hash_is_key_order_independent(self):
        a = {"lead_seniority": {"include": ["vp"]}, "company_headcount_min": 1}
        b = {"company_headcount_min": 1, "lead_seniority": {"include": ["vp"]}}
        assert frontier.params_hash(a) == frontier.params_hash(b)

    def test_hash_differs_on_value(self):
        assert frontier.params_hash({"x": 1}) != frontier.params_hash({"x": 2})


# ── seed ─────────────────────────────────────────────────────────────

class TestGenerateSeed:
    def test_returns_filters_and_folds_country_no_node(self, db):
        c = _campaign()
        spec = {"filters": {"lead_seniority": {"include": ["vp"]}}, "country_code": "gb"}
        with patch("openoutreach.core.pipeline.icp.generate_icp_spec", return_value=spec):
            filters = frontier.generate_seed(c)
        c.refresh_from_db()
        assert filters == {"lead_seniority": {"include": ["vp"]}}
        assert c.country_code == "gb"
        # the seed isn't cached and no node is created — its first fetch makes one
        assert not DiscoveryQuery.objects.filter(campaign=c).exists()

    def test_empty_spec_returns_empty(self, db):
        c = _campaign()
        with patch("openoutreach.core.pipeline.icp.generate_icp_spec",
                   return_value={"filters": {}, "country_code": ""}):
            assert frontier.generate_seed(c) == {}


# ── persist / exhaust ────────────────────────────────────────────────

class TestPersistAndExhaust:
    def test_persist_is_deduped_on_triple(self, db):
        c = _campaign()
        a = frontier.persist_fetched(c, {"x": 1}, offset=0)
        b = frontier.persist_fetched(c, {"x": 1}, offset=0)  # exact twin
        assert a.pk == b.pk
        assert DiscoveryQuery.objects.filter(campaign=c).count() == 1
        # a deeper page of the same params is a distinct node
        frontier.persist_fetched(c, {"x": 1}, offset=100)
        assert DiscoveryQuery.objects.filter(campaign=c).count() == 2

    def test_persist_leaves_node_active(self, db):
        c = _campaign()
        node = frontier.persist_fetched(c, {"x": 1}, offset=0)
        assert node.exhausted is False

    def test_mark_exhausted_flags_whole_params_line(self, db):
        c = _campaign()
        p0 = _node(c, {"x": 1}, offset=0)
        p1 = _node(c, {"x": 1}, offset=100)
        other = _node(c, {"y": 1}, offset=0)
        frontier.mark_exhausted(c, {"x": 1})
        p0.refresh_from_db(); p1.refresh_from_db(); other.refresh_from_db()
        assert p0.exhausted and p1.exhausted  # every offset of the line
        assert not other.exhausted             # a different query is untouched


# ── the node metric ──────────────────────────────────────────────────

class TestNodeStats:
    def test_counts_examined_and_qualified(self, db):
        c = _campaign()
        node = _node(c, {"x": 1})
        _examined(c, node, "a")
        _examined(c, node, "b")
        _rejected(c, node, "c")
        assert frontier.node_stats(c)[node.pk] == frontier.NodeStats(3, 2)

    def test_unexamined_node_is_absent_not_zero(self, db):
        c = _campaign()
        node = _node(c, {"x": 1})
        _lead(node, "never-ruled-on")  # discovered, but no Deal
        # Absent, not NodeStats(0, 0) — "nobody looked" must not read as "barren".
        assert node.pk not in frontier.node_stats(c)

    def test_qualified_survives_the_lead_advancing(self, db):
        """A node's value must not fall as its leads succeed down the funnel.

        Counting ``state == QUALIFIED`` would do exactly that: the deal moves on to
        READY_TO_EMAIL and the vein would look barren the moment it started paying.
        """
        c = _campaign()
        node = _node(c, {"x": 1})
        _examined(c, node, "a", state=DealState.EMAILED)
        _examined(c, node, "b", state=DealState.COMPLETED, outcome=Outcome.CONVERTED)
        assert frontier.node_stats(c)[node.pk] == frontier.NodeStats(2, 2)

    def test_operational_failure_still_counts_as_qualified(self, db):
        """FAILED with a blank outcome is the "no email" miss — the LLM said yes."""
        c = _campaign()
        node = _node(c, {"x": 1})
        _examined(c, node, "a", state=DealState.FAILED, outcome="")
        assert frontier.node_stats(c)[node.pk] == frontier.NodeStats(1, 1)

    def test_is_scoped_to_the_campaign(self, db):
        c, other = _campaign(), _campaign(name="D")
        node = _node(c, {"x": 1})
        _examined(other, node, "a")  # same node, another campaign's deal
        assert node.pk not in frontier.node_stats(c)


# ── selection: bootstrap / deepen / wall ─────────────────────────────

class TestBootstrap:
    def test_cold_start_generates_seed_at_zero(self, db):
        c = _campaign()  # no nodes yet
        with patch("openoutreach.core.pipeline.icp.generate_icp_spec",
                   return_value={"filters": {"seed": 1}, "country_code": ""}):
            q = frontier.next_query(c)
        assert q == frontier.NextQuery({"seed": 1}, 0, "bootstrap")

    def test_deepens_seed_node_without_regenerating(self, db):
        c = _campaign()
        _node(c, {"seed": 1}, offset=0)
        _node(c, {"seed": 1}, offset=100)
        with patch("openoutreach.core.pipeline.icp.generate_icp_spec",
                   side_effect=AssertionError("seed node exists — must not regenerate")):
            q = frontier.next_query(c)
        assert q == frontier.NextQuery({"seed": 1}, 200, "bootstrap")  # max offset + one page

    def test_none_when_icp_empty(self, db):
        c = _campaign()
        with patch("openoutreach.core.pipeline.icp.generate_icp_spec",
                   return_value={"filters": {}, "country_code": ""}):
            assert frontier.next_query(c) is None

    def test_none_when_seed_exhausted(self, db):
        c = _campaign()
        _node(c, {"seed": 1}, offset=0, exhausted=True)
        assert frontier.next_query(c) is None

    def test_unexamined_nodes_do_not_end_bootstrap(self, db):
        """Leads nobody has ruled on are not a verdict — keep paging the seed.

        Were unexamined read as zero, a walled-into region would look barren the
        instant it was fetched and the walk would wall again off no evidence.
        """
        c = _campaign()
        _node(c, {"seed": 1}, offset=0)
        fresh = _node(c, {"new": 1}, offset=0)
        _lead(fresh, "unruled")
        with patch("openoutreach.core.pipeline.mutate.generate_mutation",
                   side_effect=AssertionError("unexamined must not read as a wall")):
            q = frontier.next_query(c)
        assert q == frontier.NextQuery({"seed": 1}, 100, "bootstrap")


class TestDeepen:
    def test_deepens_the_node_with_most_qualified(self, db):
        c = _campaign()
        cold = _node(c, {"a": 1}, offset=0)
        _examined(c, cold, "a1")
        _rejected(c, cold, "a2")            # 2 examined, 1 qualified
        hot = _node(c, {"b": 1}, offset=0)
        for tag in ("b1", "b2", "b3"):
            _examined(c, hot, tag)          # 3 examined, 3 qualified
        _node(c, {"b": 1}, offset=100)      # deepest page of the hot line
        q = frontier.next_query(c)
        assert q == frontier.NextQuery({"b": 1}, 200, "deepen")

    def test_skips_exhausted_productive_node(self, db):
        c = _campaign()
        alive = _node(c, {"a": 1}, offset=0)
        _examined(c, alive, "a1")
        dry = _node(c, {"b": 1}, offset=0, exhausted=True)  # richer, but dried up
        for tag in ("b1", "b2", "b3"):
            _examined(c, dry, tag)
        q = frontier.next_query(c)
        assert q.params == {"a": 1} and q.move == "deepen"

    def test_a_seed_that_pays_is_deepened_not_walled_away_from(self, db):
        """The bug this whole change exists to kill.

        The old score asked the GP how many of a node's leads cleared 0.9 — none
        ever did, so a seed with real qualified leads scored 0, read as a wall, and
        got mutated away from. ``deepen`` never fired on any node, ever.
        """
        c = _campaign()
        seed = _node(c, {"seed": 1}, offset=0)
        _examined(c, seed, "won")
        for tag in ("lost1", "lost2", "lost3"):
            _rejected(c, seed, tag)  # a thin 1-in-4 vein is still a vein
        with patch("openoutreach.core.pipeline.mutate.generate_mutation",
                   side_effect=AssertionError("a paying node must be deepened, not abandoned")):
            q = frontier.next_query(c)
        assert q == frontier.NextQuery({"seed": 1}, 100, "deepen")


class TestWall:
    def test_all_examined_and_none_qualified_asks_llm(self, db):
        c = _campaign()
        node = _node(c, {"a": 1}, offset=0)
        _rejected(c, node, "a1")
        with patch("openoutreach.core.pipeline.mutate.generate_mutation",
                   return_value={"new": 1}) as gen:
            q = frontier.next_query(c)
        gen.assert_called_once()
        assert q == frontier.NextQuery({"new": 1}, 0, "wall")

    def test_none_when_llm_dry(self, db):
        c = _campaign()
        node = _node(c, {"a": 1}, offset=0)
        _rejected(c, node, "a1")
        with patch("openoutreach.core.pipeline.mutate.generate_mutation", return_value={}):
            assert frontier.next_query(c) is None

    def test_none_when_llm_reproposes_tried_query(self, db):
        c = _campaign()
        node = _node(c, {"a": 1}, offset=0)
        _rejected(c, node, "a1")
        with patch("openoutreach.core.pipeline.mutate.generate_mutation",
                   return_value={"a": 1}):
            assert frontier.next_query(c) is None
