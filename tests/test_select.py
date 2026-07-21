# tests/test_select.py
"""Query selection — the GP-scored maximal walk.

Clauses are axes; the only queries are maximals (one value per family). The GP scores
each candidate's keywords and argmax wins; an unfitted GP (acquisition → None) falls
back to seed-first, fresh-before-deep. ``embed_query`` is stubbed so no ONNX model is
touched; the fake GP returns scores directly, independent of the embeddings.
"""
from unittest.mock import patch

import numpy as np

from openoutreach.core.models import Clause, DiscoveryQuery, EmptyClauseSet
from openoutreach.core.pipeline import select
from openoutreach.core.pipeline.select import (
    NextQuery, _maximals, _pool, clause_key, mark_exhausted, next_query,
    persist_fetched, record_empty,
)


def _campaign(**kw):
    from openoutreach.core.models import Campaign

    defaults = dict(name="C", product_docs="p", campaign_target="t")
    defaults.update(kw)
    return Campaign.objects.create(**defaults)


class _GP:
    """A fitted GP returning fixed acquisition scores over the candidates.

    The prefilter ranks by ``predict_probs`` (exploit) or ``posterior_std`` (explore);
    both return per-candidate values so the top-K slice — and the final argmax over the
    exact-embedded subset — land on the same fixed scores. Small test pools fit within K,
    so the subset is the whole (seed-first) candidate list and index ``i`` still maps to
    candidate ``i``.
    """

    def __init__(self, scores, mode="exploit (p)"):
        self._scores = scores
        self._mode = mode

    def _slice(self, embeddings):
        return np.array(self._scores[: len(embeddings)], dtype=np.float64)

    def acquisition_mode(self):
        return self._mode

    def predict_probs(self, embeddings):
        return self._slice(embeddings)

    def posterior_std(self, embeddings):
        return np.ones(len(embeddings), dtype=np.float64)

    def acquisition_scores(self, embeddings):
        return self._mode, self._slice(embeddings)


class _ColdGP:
    """An unfitted GP — no signal, so selection uses the deterministic fallback."""

    def acquisition_mode(self):
        return None

    def acquisition_scores(self, embeddings):
        return None


def _stub_embed():
    return patch("openoutreach.core.pipeline.select.embed_queries",
                 side_effect=lambda sets: np.ones((len(list(sets)), 384), dtype=np.float64))


# ── the maximals the pool spans ──────────────────────────────────────


class TestMaximals:
    def test_cartesian_product_one_value_per_family(self, db):
        campaign = _campaign()
        campaign.clauses.set(Clause.rows_for([
            ("lead_job_title", "CMO"), ("lead_job_title", "CTO"),
            ("lead_location", "Japan"),
        ]))
        maximals = _maximals(_pool(campaign))
        assert maximals == [
            [("lead_job_title", "CMO"), ("lead_location", "Japan")],
            [("lead_job_title", "CTO"), ("lead_location", "Japan")],
        ]

    def test_empty_pool_selects_nothing(self, db):
        assert next_query(_campaign(), _ColdGP()) is None


# ── selection ────────────────────────────────────────────────────────


class TestNextQuery:
    def test_cold_start_picks_seed_first_fresh_maximal(self, db):
        campaign = _campaign()
        campaign.clauses.set(Clause.rows_for([
            ("lead_job_title", "CMO"), ("lead_job_title", "CTO"),
            ("lead_location", "Japan"),
        ]))
        with _stub_embed():
            q = next_query(campaign, _ColdGP())
        # CMO was added first → lowest pool rank → the seed-closest maximal, offset 0
        assert q == NextQuery([("lead_job_title", "CMO"), ("lead_location", "Japan")], 0)

    def test_gp_argmax_wins(self, db):
        campaign = _campaign()
        campaign.clauses.set(Clause.rows_for([
            ("lead_job_title", "CMO"), ("lead_job_title", "CTO"),
            ("lead_location", "Japan"),
        ]))
        # Candidates in (offset, rank) order: [CMO·Japan, CTO·Japan]. Score the second higher.
        with _stub_embed():
            q = next_query(campaign, _GP([0.1, 0.9]))
        assert q.clauses == [("lead_job_title", "CTO"), ("lead_location", "Japan")]

    def test_explore_mode_argmax_wins(self, db):
        campaign = _campaign()
        campaign.clauses.set(Clause.rows_for([
            ("lead_job_title", "CMO"), ("lead_job_title", "CTO"),
            ("lead_location", "Japan"),
        ]))
        # Explore prefilter ranks by posterior_std; final argmax is over BALD scores.
        with _stub_embed():
            q = next_query(campaign, _GP([0.1, 0.9], mode="explore (BALD)"))
        assert q.clauses == [("lead_job_title", "CTO"), ("lead_location", "Japan")]

    def test_fetched_line_becomes_a_deepen_candidate(self, db):
        campaign = _campaign()
        seed = [("lead_location", "Japan")]
        campaign.clauses.set(Clause.rows_for(seed))
        persist_fetched(campaign, seed, offset=0)  # already fetched, not exhausted
        with _stub_embed():
            q = next_query(campaign, _ColdGP())
        assert q == NextQuery(seed, 100)  # its next page

    def test_exhausted_line_is_not_a_candidate(self, db):
        campaign = _campaign()
        seed = [("lead_location", "Japan")]
        campaign.clauses.set(Clause.rows_for(seed))
        persist_fetched(campaign, seed, offset=0)
        mark_exhausted(campaign, seed)
        with _stub_embed():
            assert next_query(campaign, _ColdGP()) is None  # nothing left → saturated

    def test_a_recorded_empty_subset_prunes_the_maximal(self, db):
        # Anti-monotone: a maximal whose subset is recorded empty is dead without a
        # fetch — the prune that survives a mint adding a family.
        campaign = _campaign()
        campaign.clauses.set(Clause.rows_for([
            ("lead_job_title", "CMO"), ("lead_location", "Japan"),
        ]))
        record_empty([("lead_job_title", "CMO")])
        with _stub_embed():
            assert next_query(campaign, _ColdGP()) is None


# ── prefilter ────────────────────────────────────────────────────────


class TestPrefilter:
    def test_keeps_only_top_k_on_the_live_axis(self, db, monkeypatch):
        campaign = _campaign()
        campaign.clauses.set(Clause.rows_for([
            ("lead_job_title", "CMO"), ("lead_job_title", "CTO"),
            ("lead_job_title", "CFO"), ("lead_location", "Japan"),
        ]))
        candidates = select._candidates(campaign, _pool(campaign))  # 3 maximals
        assert len(candidates) == 3
        monkeypatch.setitem(select.PREFILTER_K, "exploit (p)", 2)
        # Scores align to _candidates order [CMO, CTO, CFO]·Japan — CMO is lowest.
        with _stub_embed():
            kept = select._prefilter(candidates, _GP([0.2, 0.9, 0.5]), "exploit (p)")
        assert len(kept) == 2
        assert candidates[0] not in kept  # the lowest-scored maximal is dropped

    def test_returns_all_when_pool_fits_within_k(self, db):
        campaign = _campaign()
        campaign.clauses.set(Clause.rows_for([
            ("lead_job_title", "CMO"), ("lead_job_title", "CTO"),
            ("lead_location", "Japan"),
        ]))
        candidates = select._candidates(campaign, _pool(campaign))
        with _stub_embed():
            kept = select._prefilter(candidates, _GP([0.1, 0.9]), "exploit (p)")
        assert kept == candidates  # 2 ≤ K → unchanged, order preserved


# ── persistence primitives ───────────────────────────────────────────


class TestPersistence:
    def test_persist_fetched_dedups_and_sets_clauses(self, db):
        campaign = _campaign()
        seed = [("lead_location", "Japan")]
        a = persist_fetched(campaign, seed, 0)
        b = persist_fetched(campaign, seed, 0)
        assert a.pk == b.pk  # deduped on (campaign, clause_key, offset)
        assert a.clause_pairs == seed

    def test_mark_exhausted_flags_every_offset_of_a_line(self, db):
        campaign = _campaign()
        seed = [("lead_location", "Japan")]
        persist_fetched(campaign, seed, 0)
        persist_fetched(campaign, seed, 100)
        mark_exhausted(campaign, seed)
        assert DiscoveryQuery.objects.filter(
            campaign=campaign, clause_key=clause_key(seed), exhausted=True,
        ).count() == 2

    def test_record_empty_is_global_and_idempotent(self, db):
        seed = [("lead_location", "Europe")]
        record_empty(seed)
        record_empty(seed)
        assert EmptyClauseSet.objects.count() == 1
        assert EmptyClauseSet.objects.get().clause_pairs == seed
