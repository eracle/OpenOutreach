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
    """A fitted GP returning fixed acquisition scores over the candidates."""

    def __init__(self, scores):
        self._scores = scores

    def acquisition_scores(self, embeddings):
        return "exploit (p)", np.array(self._scores[: len(embeddings)])


class _ColdGP:
    """An unfitted GP — no signal, so selection uses the deterministic fallback."""

    def acquisition_scores(self, embeddings):
        return None


def _stub_embed():
    return patch("openoutreach.core.pipeline.select.embed_query",
                 return_value=np.ones(384, dtype=np.float64))


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
