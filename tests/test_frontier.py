# tests/test_frontier.py
"""Discovery frontier — the best-first search primitives over DiscoveryQuery
nodes: params identity + dedup, seeding, per-move re-rank/scoring, the
breadth-vs-exploit pick, expansion, retirement, and the size cap.

The qualifier is stubbed at its ``class_counts`` / ``predict_probs`` boundary so
these tests never fit a GP."""
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from openoutreach.core.ml.qualifier import BayesianQualifier
from openoutreach.core.models import Campaign, DiscoveryQuery
from openoutreach.core.pipeline import frontier

Status = DiscoveryQuery.Status


# ── helpers ──────────────────────────────────────────────────────────

def _campaign(**kw):
    defaults = dict(name="C", product_docs="widgets", campaign_target="demos")
    defaults.update(kw)
    return Campaign.objects.create(**defaults)


def _node(campaign, params, offset=0, parent=None, status=Status.PENDING, score=None):
    return DiscoveryQuery.objects.create(
        campaign=campaign, params=params, params_hash=frontier.params_hash(params),
        offset=offset, parent=parent, status=status, score=score,
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


# ── enqueue / seed ───────────────────────────────────────────────────

class TestEnqueue:
    def test_dedups_on_params_and_offset(self, db):
        c = _campaign()
        assert frontier.enqueue(c, {"x": 1}, offset=0) is not None
        assert frontier.enqueue(c, {"x": 1}, offset=0) is None  # exact twin
        assert frontier.enqueue(c, {"x": 1}, offset=100) is not None  # deeper is distinct

    def test_dedups_against_fetched_twin(self, db):
        c = _campaign()
        _node(c, {"x": 1}, status=Status.FETCHED)
        assert frontier.enqueue(c, {"x": 1}, offset=0) is None  # already visited


class TestEnsureSeed:
    def test_seeds_one_node_and_folds_country(self, db):
        c = _campaign()
        spec = {"filters": {"lead_seniority": {"include": ["vp"]}}, "country_code": "gb"}
        with patch("openoutreach.core.pipeline.icp.generate_icp_spec", return_value=spec):
            frontier.ensure_seed(c)
        c.refresh_from_db()
        assert c.country_code == "gb"
        nodes = DiscoveryQuery.objects.filter(campaign=c)
        assert nodes.count() == 1
        seed = nodes.get()
        assert seed.status == Status.PENDING and seed.offset == 0 and seed.parent is None

    def test_noop_when_frontier_already_seeded(self, db):
        c = _campaign()
        _node(c, {"x": 1})
        with patch("openoutreach.core.pipeline.icp.generate_icp_spec",
                   side_effect=AssertionError("must not regenerate")):
            frontier.ensure_seed(c)
        assert DiscoveryQuery.objects.filter(campaign=c).count() == 1

    def test_empty_spec_seeds_nothing(self, db):
        c = _campaign()
        with patch("openoutreach.core.pipeline.icp.generate_icp_spec",
                   return_value={"filters": {}, "country_code": ""}):
            frontier.ensure_seed(c)
        assert not DiscoveryQuery.objects.filter(campaign=c).exists()


# ── scoring: mark_fetched / rerank ───────────────────────────────────

class TestScoring:
    def test_mark_fetched_scores_from_leads_in_exploit(self, db):
        c = _campaign()
        node = _node(c, {"x": 1})
        _lead(c, node, 1)
        _lead(c, node, 2)
        frontier.mark_fetched(node, _exploit_qualifier(probs=[0.95, 0.10]))
        node.refresh_from_db()
        assert node.status == Status.FETCHED
        assert node.score == 1  # one lead clears the 0.9 acceptance gate

    def test_mark_fetched_leaves_score_null_pre_exploit(self, db):
        c = _campaign()
        node = _node(c, {"x": 1})
        _lead(c, node, 1)
        frontier.mark_fetched(node, _explore_qualifier())
        node.refresh_from_db()
        assert node.status == Status.FETCHED and node.score is None

    def test_rerank_updates_fetched_scores_in_exploit(self, db):
        c = _campaign()
        node = _node(c, {"x": 1}, status=Status.FETCHED, score=99)
        _lead(c, node, 1)
        frontier.rerank(c, _exploit_qualifier(probs=[0.95]))
        node.refresh_from_db()
        assert node.score == 1

    def test_rerank_is_noop_pre_exploit(self, db):
        c = _campaign()
        node = _node(c, {"x": 1}, status=Status.FETCHED, score=7)
        frontier.rerank(c, _explore_qualifier())
        node.refresh_from_db()
        assert node.score == 7  # untouched — ranking untrusted pre-exploit


# ── pick ─────────────────────────────────────────────────────────────

class TestPick:
    def test_none_when_no_pending(self, db):
        c = _campaign()
        _node(c, {"x": 1}, status=Status.FETCHED)
        assert frontier.pick(c, _explore_qualifier()) is None

    def test_explore_is_breadth_first_mutations_before_deepens(self, db):
        c = _campaign()
        parent = _node(c, {"seed": 1}, status=Status.FETCHED)
        deepen = _node(c, {"seed": 1}, offset=100, parent=parent)   # a depth move
        mutation = _node(c, {"new": 1}, offset=0, parent=parent)    # a breadth move
        picked = frontier.pick(c, _explore_qualifier())
        assert picked.pk == mutation.pk  # offset 0 (breadth) wins over the deepen

    def test_exploit_picks_highest_parent_score(self, db):
        c = _campaign()
        hot = _node(c, {"a": 1}, status=Status.FETCHED, score=9)
        cold = _node(c, {"b": 1}, status=Status.FETCHED, score=1)
        child_cold = _node(c, {"b": 1}, offset=100, parent=cold)
        child_hot = _node(c, {"a": 1}, offset=100, parent=hot)
        with patch.object(frontier, "EXPLORE_EVERY", 999):  # disable the explore reservation
            picked = frontier.pick(c, _exploit_qualifier())
        assert picked.pk == child_hot.pk

    def test_exploit_reserves_explore_pick(self, db):
        c = _campaign()
        hot = _node(c, {"a": 1}, status=Status.FETCHED, score=9)
        top_exploit = _node(c, {"a": 1}, offset=100, parent=hot)  # best exploit candidate
        fresh = _node(c, {"new": 1}, offset=0, parent=hot)        # a brand-new region
        # 1 fetched node → n_fetched % EXPLORE_EVERY(1) == 0 → the reserved explore pick
        with patch.object(frontier, "EXPLORE_EVERY", 1):
            picked = frontier.pick(c, _exploit_qualifier())
        assert picked.pk == fresh.pk  # newest unexplored (offset-0) region, not the deepen

    def test_exploit_falls_back_to_top_when_no_fresh_region(self, db):
        c = _campaign()
        hot = _node(c, {"a": 1}, status=Status.FETCHED, score=9)
        cold = _node(c, {"b": 1}, status=Status.FETCHED, score=1)
        top_exploit = _node(c, {"a": 1}, offset=100, parent=hot)
        low = _node(c, {"b": 1}, offset=100, parent=cold)
        # explore move, but no offset-0 node exists → exploit the top-ranked instead
        with patch.object(frontier, "EXPLORE_EVERY", 1):
            picked = frontier.pick(c, _exploit_qualifier())
        assert picked.pk == top_exploit.pk


# ── expand ───────────────────────────────────────────────────────────

class TestExpand:
    def test_mutates_by_default_pre_exploit(self, db):
        c = _campaign()
        node = _node(c, {"x": 1}, status=Status.FETCHED)
        with patch("openoutreach.core.pipeline.mutate.generate_mutation",
                   return_value={"new": 1}) as gen:
            frontier.expand(c, node, _explore_qualifier())
        gen.assert_called_once()
        assert DiscoveryQuery.objects.filter(campaign=c, params={"new": 1}, offset=0).exists()
        # breadth is the whole move — no deepen child (one node in, one out)
        assert not DiscoveryQuery.objects.filter(campaign=c, params={"x": 1}, offset=100).exists()

    def test_adds_at_most_one_node(self, db):
        c = _campaign()
        node = _node(c, {"x": 1}, status=Status.FETCHED)
        before = DiscoveryQuery.objects.filter(campaign=c).count()
        with patch("openoutreach.core.pipeline.mutate.generate_mutation", return_value={"new": 1}):
            frontier.expand(c, node, _explore_qualifier())
        assert DiscoveryQuery.objects.filter(campaign=c).count() == before + 1

    def test_deepens_as_fallback_when_llm_dry_pre_exploit(self, db):
        c = _campaign()
        node = _node(c, {"x": 1}, status=Status.FETCHED)
        with patch("openoutreach.core.pipeline.mutate.generate_mutation", return_value={}):
            frontier.expand(c, node, _explore_qualifier())
        assert DiscoveryQuery.objects.filter(campaign=c, params={"x": 1}, offset=100).exists()

    def test_exploit_deepens_productive_node(self, db):
        c = _campaign()
        node = _node(c, {"x": 1}, status=Status.FETCHED, score=3)
        with patch("openoutreach.core.pipeline.mutate.generate_mutation") as gen:
            frontier.expand(c, node, _exploit_qualifier())
        gen.assert_not_called()  # productive vein → mine depth, don't spend an LLM call
        assert DiscoveryQuery.objects.filter(campaign=c, params={"x": 1}, offset=100).exists()

    def test_exploit_barren_node_mutates_instead_of_deepening(self, db):
        c = _campaign()
        node = _node(c, {"x": 1}, status=Status.FETCHED, score=0)
        with patch("openoutreach.core.pipeline.mutate.generate_mutation", return_value={"new": 1}):
            frontier.expand(c, node, _exploit_qualifier())
        assert DiscoveryQuery.objects.filter(campaign=c, params={"new": 1}, offset=0).exists()
        assert not DiscoveryQuery.objects.filter(campaign=c, params={"x": 1}, offset=100).exists()

    def test_exploit_barren_node_llm_dry_adds_nothing(self, db):
        c = _campaign()
        node = _node(c, {"x": 1}, status=Status.FETCHED, score=0)
        with patch("openoutreach.core.pipeline.mutate.generate_mutation", return_value={}):
            frontier.expand(c, node, _exploit_qualifier())
        # barren region, no fresh idea, and depth isn't worth it → no new node
        assert not DiscoveryQuery.objects.filter(campaign=c, offset=100).exists()
        assert DiscoveryQuery.objects.filter(campaign=c).count() == 1


# ── retire / size-cap ────────────────────────────────────────────────

class TestRetireAndCap:
    def test_retire(self, db):
        c = _campaign()
        node = _node(c, {"x": 1})
        frontier.retire(node)
        node.refresh_from_db()
        assert node.status == Status.RETIRED

    def test_size_cap_evicts_lowest_scored_fetched(self, db):
        c = _campaign()
        keep = _node(c, {"a": 1}, status=Status.FETCHED, score=9)
        drop = _node(c, {"b": 1}, status=Status.FETCHED, score=1)
        fringe = _node(c, {"c": 1}, status=Status.PENDING)  # never evicted
        with patch.object(frontier, "FRONTIER_SIZE_CAP", 2):
            frontier.enforce_size_cap(c)
        keep.refresh_from_db(); drop.refresh_from_db(); fringe.refresh_from_db()
        assert drop.status == Status.RETIRED
        assert keep.status == Status.FETCHED
        assert fringe.status == Status.PENDING

    def test_size_cap_noop_under_budget(self, db):
        c = _campaign()
        _node(c, {"a": 1}, status=Status.FETCHED, score=1)
        with patch.object(frontier, "FRONTIER_SIZE_CAP", 200):
            frontier.enforce_size_cap(c)
        assert DiscoveryQuery.objects.filter(campaign=c, status=Status.FETCHED).count() == 1
