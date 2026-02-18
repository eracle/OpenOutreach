# tests/ml/test_qualifier.py
"""Tests for BayesianQualifier (GPC backend) and LLM qualification."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import numpy as np
import pytest

from linkedin.ml.qualifier import BayesianQualifier, _binary_entropy


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
        qualifier._ensure_fitted()
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
        assert qualifier._ensure_fitted() is True


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
        scores = qualifier.bald_scores(embeddings)
        assert scores is not None
        assert scores.shape == (5,)

    def test_bald_nonnegative(self):
        qualifier, _, _ = _make_trained_qualifier()
        embeddings = np.random.randn(5, 384).astype(np.float32)
        scores = qualifier.bald_scores(embeddings)
        assert scores is not None
        assert np.all(scores >= -1e-10)

    def test_bald_upper_bound(self):
        """Predictive entropy cannot exceed ln(2) ~ 0.693."""
        qualifier, _, _ = _make_trained_qualifier()
        embeddings = np.random.randn(5, 384).astype(np.float32)
        scores = qualifier.bald_scores(embeddings)
        assert scores is not None
        assert np.all(scores <= np.log(2) + 0.01)

    def test_bald_returns_none_when_unfitted(self):
        qualifier = BayesianQualifier(seed=42)
        embeddings = np.random.randn(5, 384).astype(np.float32)
        assert qualifier.bald_scores(embeddings) is None


class TestRankProfiles:
    def test_rank_profiles_empty(self):
        qualifier = BayesianQualifier(seed=42)
        assert qualifier.rank_profiles([]) == []

    def test_rank_profiles_orders_by_posterior(self, embeddings_db):
        qualifier, pos_emb, neg_emb = _make_trained_qualifier()

        from linkedin.ml.embeddings import store_embedding
        store_embedding(1, "positive", pos_emb)
        store_embedding(2, "negative", neg_emb)

        profiles = [
            {"public_identifier": "negative"},
            {"public_identifier": "positive"},
        ]
        ranked = qualifier.rank_profiles(profiles)
        assert ranked[0]["public_identifier"] == "positive"


class TestWarmStart:
    def test_warm_start_fits_model(self):
        rng = np.random.RandomState(99)
        X = rng.randn(20, 384).astype(np.float32)
        y = np.array([i % 2 for i in range(20)], dtype=np.int32)

        qualifier = BayesianQualifier(seed=42)
        qualifier.warm_start(X, y)

        assert qualifier.n_obs == 20
        assert qualifier._fitted is True

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
    def test_explain_no_embedding(self, embeddings_db):
        qualifier = BayesianQualifier(seed=42)
        profile = {"public_identifier": "nonexistent"}
        explanation = qualifier.explain_profile(profile)
        assert "no embedding" in explanation.lower()

    def test_explain_with_embedding(self, embeddings_db):
        from linkedin.ml.embeddings import store_embedding

        qualifier, pos_emb, _ = _make_trained_qualifier()
        store_embedding(1, "alice", pos_emb)

        profile = {"public_identifier": "alice"}
        explanation = qualifier.explain_profile(profile)
        assert "predictive" in explanation.lower()
        assert "entropy" in explanation.lower()

    def test_explain_unfitted(self, embeddings_db):
        from linkedin.ml.embeddings import store_embedding

        qualifier = BayesianQualifier(seed=42)
        emb = np.ones(384, dtype=np.float32)
        store_embedding(1, "alice", emb)

        profile = {"public_identifier": "alice"}
        explanation = qualifier.explain_profile(profile)
        assert "not fitted" in explanation.lower()
