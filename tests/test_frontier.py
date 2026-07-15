# tests/test_frontier.py
"""Discovery frontier — the lazy best-first walk over DiscoveryQuery nodes:
params identity + dedup, seeding, per-move re-rank/scoring, the
bootstrap/deepen/wall selector, node persistence, and reactive exhaustion.

The qualifier is stubbed at its ``class_counts`` / ``predict_probs`` boundary so
these tests never fit a GP."""
from unittest.mock import MagicMock, patch

import numpy as np

from openoutreach.core.ml.qualifier import BayesianQualifier
from openoutreach.core.models import Campaign, DiscoveryQuery
from openoutreach.core.pipeline import frontier


# ── helpers ──────────────────────────────────────────────────────────

def _campaign(**kw):
    defaults = dict(name="C", product_docs="widgets", campaign_target="demos")
    defaults.update(kw)
    return Campaign.objects.create(**defaults)


def _node(campaign, params, offset=0, score=None, exhausted=False):
    return DiscoveryQuery.objects.create(
        campaign=campaign, params=params, params_hash=frontier.params_hash(params),
        offset=offset, score=score, exhausted=exhausted,
    )


def _lead(campaign, node, emb):
    """A first-touch lead for ``node`` carrying a 384-dim embedding."""
    from openoutreach.crm.models import Lead

    vec = np.zeros(384, dtype=np.float32)
    vec[0] = emb
    return Lead.objects.create(
        profile_url=f"https://x/{node.pk}-{emb}/", discovered_by=node,
        embedding=vec.tobytes(),
    )


def _explore_qualifier():
    """Pre-exploit: negatives do NOT outnumber positives."""
    q = MagicMock(spec=BayesianQualifier)
    q.class_counts = (0, 0)
    return q


def _exploit_qualifier(probs=None):
    """Exploit mode; ``predict_probs`` returns a fixed array (probs > 0.9 accept)."""
    q = MagicMock(spec=BayesianQualifier)
    q.class_counts = (5, 2)
    q.predict_probs.return_value = None if probs is None else np.asarray(probs, dtype=float)
    return q


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

    def test_persist_leaves_score_null(self, db):
        c = _campaign()
        node = frontier.persist_fetched(c, {"x": 1}, offset=0)
        assert node.score is None and node.exhausted is False

    def test_mark_exhausted_flags_whole_params_line(self, db):
        c = _campaign()
        p0 = _node(c, {"x": 1}, offset=0)
        p1 = _node(c, {"x": 1}, offset=100)
        other = _node(c, {"y": 1}, offset=0)
        frontier.mark_exhausted(c, {"x": 1})
        p0.refresh_from_db(); p1.refresh_from_db(); other.refresh_from_db()
        assert p0.exhausted and p1.exhausted  # every offset of the line
        assert not other.exhausted             # a different query is untouched


# ── scoring: rerank ──────────────────────────────────────────────────

class TestRerank:
    def test_rerank_scores_from_leads_in_exploit(self, db):
        c = _campaign()
        node = _node(c, {"x": 1}, score=99)
        _lead(c, node, 1)
        _lead(c, node, 2)
        frontier.rerank(c, _exploit_qualifier(probs=[0.95, 0.10]))
        node.refresh_from_db()
        assert node.score == 1  # one lead clears the 0.9 acceptance gate

    def test_rerank_is_noop_pre_exploit(self, db):
        c = _campaign()
        node = _node(c, {"x": 1}, score=7)
        frontier.rerank(c, _explore_qualifier())
        node.refresh_from_db()
        assert node.score == 7  # untouched — ranking untrusted pre-exploit

    def test_rerank_skips_exhausted_nodes(self, db):
        c = _campaign()
        node = _node(c, {"x": 1}, score=42, exhausted=True)
        _lead(c, node, 1)
        frontier.rerank(c, _exploit_qualifier(probs=[0.95]))
        node.refresh_from_db()
        assert node.score == 42  # exhausted lines drop out of the re-rank


# ── selection: bootstrap / deepen / wall ─────────────────────────────

class TestBootstrap:
    def test_cold_start_generates_seed_at_zero(self, db):
        c = _campaign()  # no nodes yet
        with patch("openoutreach.core.pipeline.icp.generate_icp_spec",
                   return_value={"filters": {"seed": 1}, "country_code": ""}):
            q = frontier.next_query(c, _explore_qualifier())
        assert q == frontier.NextQuery({"seed": 1}, 0, "bootstrap")

    def test_deepens_seed_node_without_regenerating(self, db):
        c = _campaign()
        _node(c, {"seed": 1}, offset=0)
        _node(c, {"seed": 1}, offset=100)
        with patch("openoutreach.core.pipeline.icp.generate_icp_spec",
                   side_effect=AssertionError("seed node exists — must not regenerate")):
            q = frontier.next_query(c, _explore_qualifier())
        assert q == frontier.NextQuery({"seed": 1}, 200, "bootstrap")  # max offset + one page

    def test_none_when_icp_empty(self, db):
        c = _campaign()
        with patch("openoutreach.core.pipeline.icp.generate_icp_spec",
                   return_value={"filters": {}, "country_code": ""}):
            assert frontier.next_query(c, _explore_qualifier()) is None

    def test_none_when_seed_exhausted(self, db):
        c = _campaign()
        _node(c, {"seed": 1}, offset=0, exhausted=True)
        assert frontier.next_query(c, _explore_qualifier()) is None


class TestDeepen:
    def test_exploit_deepens_highest_scoring_node(self, db):
        c = _campaign()
        _node(c, {"a": 1}, offset=0, score=2)
        hot = _node(c, {"b": 1}, offset=0, score=9)
        _node(c, {"b": 1}, offset=100, score=5)  # deepest page of the hot line
        q = frontier.next_query(c, _exploit_qualifier())
        assert q == frontier.NextQuery({"b": 1}, 200, "deepen")  # deepen the hot line

    def test_exploit_skips_exhausted_positive_node(self, db):
        c = _campaign()
        _node(c, {"a": 1}, offset=0, score=3)
        _node(c, {"b": 1}, offset=0, score=9, exhausted=True)  # higher, but dried up
        q = frontier.next_query(c, _exploit_qualifier())
        assert q.params == {"a": 1} and q.move == "deepen"


class TestWall:
    def test_all_zero_asks_llm_for_new_query(self, db):
        c = _campaign()
        _node(c, {"a": 1}, offset=0, score=0)
        with patch("openoutreach.core.pipeline.mutate.generate_mutation",
                   return_value={"new": 1}) as gen:
            q = frontier.next_query(c, _exploit_qualifier())
        gen.assert_called_once()
        assert q == frontier.NextQuery({"new": 1}, 0, "wall")

    def test_none_when_llm_dry(self, db):
        c = _campaign()
        _node(c, {"a": 1}, offset=0, score=0)
        with patch("openoutreach.core.pipeline.mutate.generate_mutation", return_value={}):
            assert frontier.next_query(c, _exploit_qualifier()) is None

    def test_none_when_llm_reproposes_tried_query(self, db):
        c = _campaign()
        _node(c, {"a": 1}, offset=0, score=0)  # already tried, scored 0
        with patch("openoutreach.core.pipeline.mutate.generate_mutation",
                   return_value={"a": 1}):
            assert frontier.next_query(c, _exploit_qualifier()) is None
