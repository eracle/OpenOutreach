# tests/ml/test_qualifier.py
"""Tests for BayesianQualifier (GP Regression backend) and LLM qualification."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import numpy as np
import pytest

from openoutreach.core.ml.qualifier import BayesianQualifier, _binary_entropy


def _make_trained_qualifier(n_pos=10, n_neg=10, seed=42):
    """Create a qualifier with both classes so the GPC can fit."""
    qualifier = BayesianQualifier(seed=seed)
    rng = np.random.RandomState(seed)
    pos_emb = rng.randn(384).astype(np.float32) + 1.0
    neg_emb = rng.randn(384).astype(np.float32) - 1.0
    for _ in range(n_pos):
        qualifier.update(pos_emb + rng.randn(384).astype(np.float32) * 0.1, 1)
    for _ in range(n_neg):
        qualifier.update(neg_emb + rng.randn(384).astype(np.float32) * 0.1, 0)
    return qualifier, pos_emb, neg_emb


class TestBayesianQualifierUpdate:
    def test_update_increments_n_obs(self):
        qualifier = BayesianQualifier(seed=42)
        emb = np.random.randn(384).astype(np.float32)
        qualifier.update(emb, 1)
        assert qualifier.n_obs == 1

    def test_update_invalidates_fit(self):
        qualifier, _, _ = _make_trained_qualifier()
        qualifier._fit_if_needed()
        assert qualifier._fitted is True
        qualifier.update(np.random.randn(384).astype(np.float32), 1)
        assert qualifier._fitted is False

    def test_update_grows_training_data(self):
        qualifier = BayesianQualifier(seed=42)
        for i in range(50):
            qualifier.update(np.random.randn(384).astype(np.float32), i % 2)
        assert qualifier.n_obs == 50
        assert len(qualifier._X) == 50
        assert len(qualifier._y) == 50

    def test_multiple_updates_numerically_stable(self):
        qualifier = BayesianQualifier(seed=42)
        rng = np.random.RandomState(42)
        for _ in range(100):
            emb = rng.randn(384).astype(np.float32)
            label = rng.randint(0, 2)
            qualifier.update(emb, label)
        assert qualifier.n_obs == 100
        assert qualifier._fit_if_needed() is True


class TestBayesianQualifierPredict:
    def test_predict_returns_prob_entropy_and_std(self):
        qualifier, pos_emb, _ = _make_trained_qualifier()
        result = qualifier.predict(pos_emb)
        assert result is not None
        prob, entropy, std = result
        assert 0 <= prob <= 1
        assert entropy >= 0
        assert std >= 0

    def test_predict_returns_none_when_unfitted(self):
        qualifier = BayesianQualifier(seed=42)
        emb = np.random.randn(384).astype(np.float32)
        assert qualifier.predict(emb) is None

    def test_predict_returns_none_single_class(self):
        qualifier = BayesianQualifier(seed=42)
        for _ in range(5):
            qualifier.update(np.random.randn(384).astype(np.float32), 1)
        assert qualifier.predict(np.random.randn(384).astype(np.float32)) is None

    def test_predict_shifts_after_training(self):
        qualifier, pos_emb, _ = _make_trained_qualifier(n_pos=20, n_neg=5)
        result = qualifier.predict(pos_emb)
        assert result is not None
        prob, _, _ = result
        assert prob > 0.7


class TestBaldScores:
    def test_bald_shape(self):
        qualifier, _, _ = _make_trained_qualifier()
        embeddings = np.random.randn(5, 384).astype(np.float32)
        scores = qualifier.compute_bald(embeddings)
        assert scores is not None
        assert scores.shape == (5,)

    def test_bald_nonnegative(self):
        qualifier, _, _ = _make_trained_qualifier()
        embeddings = np.random.randn(5, 384).astype(np.float32)
        scores = qualifier.compute_bald(embeddings)
        assert scores is not None
        assert np.all(scores >= -1e-10)

    def test_bald_upper_bound(self):
        """Predictive entropy cannot exceed ln(2) ~ 0.693."""
        qualifier, _, _ = _make_trained_qualifier()
        embeddings = np.random.randn(5, 384).astype(np.float32)
        scores = qualifier.compute_bald(embeddings)
        assert scores is not None
        assert np.all(scores <= np.log(2) + 0.01)

    def test_bald_returns_none_when_unfitted(self):
        qualifier = BayesianQualifier(seed=42)
        embeddings = np.random.randn(5, 384).astype(np.float32)
        assert qualifier.compute_bald(embeddings) is None


class TestRankProfiles:
    def test_rank_profiles_empty(self):
        qualifier = BayesianQualifier(seed=42)
        assert qualifier.rank_profiles([]) == []

    def test_rank_profiles_orders_by_posterior(self, db):
        from openoutreach.crm.models import Lead

        qualifier, pos_emb, neg_emb = _make_trained_qualifier()
        Lead.objects.create(
            pk=1, profile_url="https://linkedin.com/in/positive/",
            embedding=pos_emb.tobytes(),
        )
        Lead.objects.create(
            pk=2, profile_url="https://linkedin.com/in/negative/",
            embedding=neg_emb.tobytes(),
        )

        profiles = [
            {"lead_id": 2, "profile_url": "https://linkedin.com/in/negative/"},
            {"lead_id": 1, "profile_url": "https://linkedin.com/in/positive/"},
        ]
        ranked = qualifier.rank_profiles(profiles)
        assert ranked[0]["profile_url"] == "https://linkedin.com/in/positive/"


class TestWarmStart:
    def test_warm_start_loads_the_labels_and_leaves_the_fit_for_first_use(self):
        """Loading is not fitting. The anchors land *after* this and mark the model
        dirty again, so a fit here is one full GP thrown away on every construction."""
        rng = np.random.RandomState(99)
        X = rng.randn(20, 384).astype(np.float32)
        y = np.array([i % 2 for i in range(20)], dtype=np.int32)

        qualifier = BayesianQualifier(seed=42)
        qualifier.warm_start(X, y)

        assert qualifier.n_obs == 20
        assert qualifier._fitted is False

    def test_the_loaded_labels_are_what_the_first_prediction_fits(self):
        rng = np.random.RandomState(99)
        X = rng.randn(20, 384).astype(np.float32)
        y = np.array([i % 2 for i in range(20)], dtype=np.int32)

        qualifier = BayesianQualifier(seed=42)
        qualifier.warm_start(X, y)

        assert qualifier.predict(rng.randn(384).astype(np.float32)) is not None
        assert qualifier._fitted is True

    def test_the_one_fit_is_over_the_labels_and_the_anchors_together(self, caplog):
        """**The whole set, once.** The build order is load-then-anchor, and the fit
        that survives is the one after both — 20 real labels *plus* 3 anchors, never the
        20 alone. Waiting is what makes that true: an eager fit at load time could only
        ever see the smaller set, which is why the discarded fit was also the wrong one.
        """
        rng = np.random.RandomState(99)
        X = rng.randn(20, 384).astype(np.float32)
        y = np.array([i % 2 for i in range(20)], dtype=np.int32)

        qualifier = BayesianQualifier(seed=42)
        qualifier.warm_start(X, y)
        qualifier.set_anchors(rng.randn(3, 384).astype(np.float32))

        with caplog.at_level("INFO"):
            qualifier.predict(rng.randn(384).astype(np.float32))

        fits = [r.getMessage() for r in caplog.records
                if "training this campaign's ranking model" in r.getMessage()]
        assert len(fits) == 1
        assert "23 judged lead(s)" in fits[0] and "3 of them still synthetic" in fits[0]

    def test_warm_start_matches_sequential_predictions(self):
        rng = np.random.RandomState(99)
        X = rng.randn(20, 384).astype(np.float32)
        y = np.array([i % 2 for i in range(20)], dtype=np.int32)

        qualifier1 = BayesianQualifier(seed=42)
        for i in range(20):
            qualifier1.update(X[i], int(y[i]))

        qualifier2 = BayesianQualifier(seed=42)
        qualifier2.warm_start(X, y)

        test_emb = rng.randn(384).astype(np.float32)
        result1 = qualifier1.predict(test_emb)
        result2 = qualifier2.predict(test_emb)

        assert result1 is not None and result2 is not None
        np.testing.assert_allclose(result1[0], result2[0], atol=1e-6)


class TestExplainProfile:
    def test_explain_no_embedding(self, db):
        qualifier = BayesianQualifier(seed=42)
        profile = {"lead_id": 999, "profile_url": "https://linkedin.com/in/nonexistent/"}
        explanation = qualifier.explain(profile)
        assert "no embedding" in explanation.lower()

    def test_explain_with_embedding(self, db):
        from openoutreach.crm.models import Lead

        qualifier, pos_emb, _ = _make_trained_qualifier()
        Lead.objects.create(
            pk=1, profile_url="https://linkedin.com/in/alice/",
            embedding=pos_emb.tobytes(),
        )

        profile = {"lead_id": 1, "profile_url": "https://linkedin.com/in/alice/"}
        explanation = qualifier.explain(profile)
        assert "mean=" in explanation
        assert "obs=" in explanation

    def test_explain_unfitted(self, db):
        from openoutreach.crm.models import Lead

        qualifier = BayesianQualifier(seed=42)
        emb = np.ones(384, dtype=np.float32)
        Lead.objects.create(
            pk=1, profile_url="https://linkedin.com/in/alice/",
            embedding=emb.tobytes(),
        )

        profile = {"lead_id": 1, "profile_url": "https://linkedin.com/in/alice/"}
        explanation = qualifier.explain(profile)
        assert "not fitted" in explanation.lower()


class TestAnchors:
    """Synthetic positives that let a GP fit before any real lead has qualified.

    The cold phase is the state this exists for: every LLM verdict is a rejection until
    the ICP is right, and a single-class label set produces no posterior at all.
    """

    @staticmethod
    def _anchors(seed=0):
        rng = np.random.RandomState(seed)
        return rng.randn(3, 384).astype(np.float32) + 1.0

    def test_anchors_make_an_all_negative_qualifier_fit(self):
        qualifier = BayesianQualifier(seed=42)
        rng = np.random.RandomState(1)
        for _ in range(5):
            qualifier.update(rng.randn(384).astype(np.float32) - 1.0, 0)
        assert qualifier.acquisition_mode() is None  # single class — nothing to fit

        qualifier.set_anchors(self._anchors())

        assert qualifier.acquisition_mode() is not None
        assert qualifier.predict_probs(self._anchors()) is not None

    def test_anchors_count_as_positives_but_not_as_real_ones(self):
        qualifier = BayesianQualifier(seed=42)
        qualifier.update(np.zeros(384, dtype=np.float32), 0)
        qualifier.set_anchors(self._anchors())

        assert qualifier.class_counts == (1, 3)
        assert qualifier.n_obs == 4
        assert qualifier.has_real_positive is False

    def test_a_real_positive_leaves_every_anchor_standing(self):
        """The anchors are permanent — a real acceptance grows the positive class
        alongside them, it never displaces one."""
        qualifier = BayesianQualifier(seed=42)
        rng = np.random.RandomState(1)
        for _ in range(10):
            qualifier.update(rng.randn(384).astype(np.float32) - 1.0, 0)
        qualifier.set_anchors(self._anchors())

        qualifier.update(np.ones(384, dtype=np.float32), 1)

        assert qualifier.has_real_positive is True
        assert qualifier.is_cold is True          # 1 real positive < ANCHOR_COUNT (3)
        assert qualifier.class_counts == (10, 4)  # one real positive + all three anchors

    def test_the_cold_phase_ends_once_real_positives_reach_anchor_count(self):
        qualifier = BayesianQualifier(seed=42)
        rng = np.random.RandomState(1)
        for _ in range(2):
            qualifier.update(rng.randn(384).astype(np.float32) - 1.0, 0)
        qualifier.set_anchors(self._anchors())

        for _ in range(3):
            qualifier.update(np.ones(384, dtype=np.float32), 1)

        assert qualifier.is_cold is False
        assert qualifier.n_anchors == 3  # still standing — only the phase clock moved
        assert qualifier.class_counts == (2, 6)  # three real positives, three anchors
        assert qualifier.n_obs == 8

    def test_a_rejection_keeps_the_anchors(self):
        """Only real positives move the phase clock — rejections are what it is made of."""
        qualifier = BayesianQualifier(seed=42)
        qualifier.set_anchors(self._anchors())

        qualifier.update(np.zeros(384, dtype=np.float32), 0)

        assert qualifier.class_counts == (1, 3)
        assert qualifier.has_real_positive is False

    def test_anchoring_is_not_trimmed_by_prior_real_positives(self):
        """Safe to call on every daemon boot — restoring the stored set must not read the
        real positives already on the qualifier as a reason to drop any of it."""
        qualifier = BayesianQualifier(seed=42)
        qualifier.update(np.ones(384, dtype=np.float32), 1)
        qualifier.update(np.zeros(384, dtype=np.float32), 0)

        qualifier.set_anchors(self._anchors())

        assert qualifier.class_counts == (1, 4)  # one real positive + all three anchors
        assert qualifier.is_cold is True

    def test_anchoring_twice_does_not_stack(self):
        qualifier = BayesianQualifier(seed=42)
        qualifier.set_anchors(self._anchors())
        qualifier.set_anchors(self._anchors(seed=7))

        assert qualifier.class_counts == (0, 3)

    def test_real_negatives_are_not_subsampled_away_by_the_anchors(self):
        """``_balance`` caps the majority at 2x the minority. With 3 synthetic positives
        that would throw away all but 6 of the real rejections — the opposite of its job,
        so balancing is skipped until a real positive exists."""
        qualifier = BayesianQualifier(seed=42)
        rng = np.random.RandomState(2)
        for _ in range(50):
            qualifier.update(rng.randn(384).astype(np.float32) - 1.0, 0)
        qualifier.set_anchors(self._anchors())

        with patch.object(BayesianQualifier, "_balance",
                          side_effect=AssertionError("must not balance while anchored")):
            assert qualifier.acquisition_mode() is not None

    def test_warm_start_leaves_anchors_intact(self):
        """Boot order is warm_start then anchor, but neither may clobber the other."""
        qualifier = BayesianQualifier(seed=42)
        qualifier.set_anchors(self._anchors())

        qualifier.warm_start(np.zeros((2, 384), dtype=np.float32), np.array([0, 0]))

        assert qualifier.class_counts == (2, 3)
        assert qualifier.has_real_positive is False


class TestColdPhaseAcquisition:
    """While the only positives are invented, the axis is exploit — see acquisition_mode."""

    @staticmethod
    def _cold(n_rejections: int, n_anchors: int):
        rng = np.random.RandomState(0)
        q = BayesianQualifier(embedding_dim=8)
        q.set_anchors(rng.rand(n_anchors, 8))
        for _ in range(n_rejections):
            q.update(rng.rand(8), 0)
        return q

    def test_cold_phase_exploits_however_the_classes_balance(self):
        # The live bug: while the anchors were held at the rejection count, `n_neg > n_pos`
        # was never true and the axis was pinned to BALD for the whole cold phase.
        for rejections, anchors in ((6, 7), (9, 11), (50, 3)):
            q = self._cold(rejections, anchors)
            assert q.acquisition_mode() == "exploit (p)", (rejections, anchors)

    def test_the_axis_returns_to_the_balance_once_real_positives_reach_anchor_count(self):
        """A first acceptance does not end the phase — the anchors stay standing and the
        axis keeps exploiting until real positives themselves reach ANCHOR_COUNT."""
        q = self._cold(n_rejections=3, n_anchors=3)
        q.update(np.random.RandomState(1).rand(8), 1)

        assert q.has_real_positive and q.is_cold
        assert q.acquisition_mode() == "exploit (p)"

        for _ in range(2):
            q.update(np.random.RandomState(2).rand(8), 1)

        assert q.is_cold is False
        assert q.n_anchors == 3  # the anchors never left
        assert q.class_counts == (3, 6)  # three real positives plus the three anchors
        assert q.acquisition_mode() == "explore (BALD)"  # real positives caught up

    def test_unfitted_model_still_reports_no_axis(self):
        assert BayesianQualifier(embedding_dim=8).acquisition_mode() is None


@pytest.mark.django_db
class TestBuildingOneCostsOneFit:
    """A construction fits **once**, over the labels *and* the anchors together.

    It used to fit twice: `warm_start` fitted the real labels eagerly, `set_anchors`
    marked the model dirty, and the first prediction fitted again with the anchors in.
    The first fit was never asked anything — pure O(n³) waste on every build, 7s at 230
    labels on the production install where it was caught.
    """

    def _campaign_with_both_classes(self, campaign):
        from openoutreach.crm.models import Deal, DealState, Lead, Outcome

        rng = np.random.RandomState(7)
        for i in range(6):
            lead = Lead.objects.create(
                profile_url=f"https://linkedin.com/in/p{i}/",
                embedding=rng.randn(384).astype(np.float32).tobytes(),
            )
            fit = i % 2 == 0
            Deal.objects.create(
                lead=lead, campaign=campaign,
                state=DealState.QUALIFIED if fit else DealState.FAILED,
                **({} if fit else {"outcome": Outcome.WRONG_FIT}),
            )
        return campaign

    def _fits(self, caplog) -> list[str]:
        return [r.getMessage() for r in caplog.records
                if "training this campaign's ranking model" in r.getMessage()]

    def test_a_build_and_its_first_prediction_train_once(self, campaign, caplog):
        from openoutreach.core.ml.qualifier import qualifier_for

        self._campaign_with_both_classes(campaign)

        with caplog.at_level("INFO"):
            qualifier = qualifier_for(campaign)
            qualifier.predict(np.random.RandomState(0).randn(384).astype(np.float32))

        assert len(self._fits(caplog)) == 1

    def test_building_alone_trains_nothing(self, campaign, caplog):
        """Nothing is fitted until something is asked — `_score_qualified` builds one
        and may then find an empty pool to score."""
        from openoutreach.core.ml.qualifier import qualifier_for

        self._campaign_with_both_classes(campaign)

        with caplog.at_level("INFO"):
            qualifier_for(campaign)

        assert self._fits(caplog) == []
