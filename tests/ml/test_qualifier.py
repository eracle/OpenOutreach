# tests/ml/test_qualifier.py
"""Tests for BayesianQualifier and LLM qualification."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import numpy as np
import pytest

from linkedin.ml.qualifier import BayesianQualifier, _binary_entropy


class TestBayesianQualifierUpdate:
    def test_update_increments_n_obs(self):
        qualifier = BayesianQualifier(seed=42)
        emb = np.random.randn(384).astype(np.float32)
        qualifier.update(emb, 1)
        assert qualifier.n_obs == 1

    def test_update_shifts_mu(self):
        qualifier = BayesianQualifier(seed=42)
        emb = np.ones(384, dtype=np.float32)
        mu_before = qualifier.mu.copy()
        qualifier.update(emb, 1)
        assert not np.allclose(qualifier.mu, mu_before)

    def test_update_reduces_sigma_trace(self):
        qualifier = BayesianQualifier(seed=42)
        emb = np.random.randn(384).astype(np.float32)
        trace_before = np.trace(qualifier.Sigma)
        qualifier.update(emb, 1)
        trace_after = np.trace(qualifier.Sigma)
        assert trace_after < trace_before

    def test_multiple_updates_numerically_stable(self):
        qualifier = BayesianQualifier(seed=42)
        rng = np.random.RandomState(42)
        for _ in range(100):
            emb = rng.randn(384).astype(np.float32)
            label = rng.randint(0, 2)
            qualifier.update(emb, label)
        assert qualifier.n_obs == 100
        assert np.all(np.isfinite(qualifier.mu))
        assert np.all(np.isfinite(qualifier.Sigma))


class TestBayesianQualifierPredict:
    def test_predict_returns_prob_and_bald(self):
        qualifier = BayesianQualifier(seed=42)
        emb = np.random.randn(384).astype(np.float32)
        prob, bald = qualifier.predict(emb)
        assert 0 <= prob <= 1
        assert bald >= -1e-10  # allow tiny numerical noise

    def test_cold_start_prediction_near_half(self):
        qualifier = BayesianQualifier(seed=42)
        emb = np.random.randn(384).astype(np.float32)
        prob, bald = qualifier.predict(emb)
        assert abs(prob - 0.5) < 0.2

    def test_predict_shifts_after_training(self):
        qualifier = BayesianQualifier(seed=42)
        positive_emb = np.ones(384, dtype=np.float32)
        for _ in range(20):
            qualifier.update(positive_emb, 1)
        prob, _ = qualifier.predict(positive_emb)
        assert prob > 0.7


class TestBaldScores:
    def test_bald_shape(self):
        qualifier = BayesianQualifier(seed=42)
        embeddings = np.random.randn(5, 384).astype(np.float32)
        scores = qualifier.bald_scores(embeddings)
        assert scores.shape == (5,)

    def test_bald_nonnegative(self):
        qualifier = BayesianQualifier(seed=42)
        embeddings = np.random.randn(5, 384).astype(np.float32)
        scores = qualifier.bald_scores(embeddings)
        assert np.all(scores >= -1e-10)

    def test_bald_upper_bound(self):
        """BALD cannot exceed ln(2) ~ 0.693."""
        qualifier = BayesianQualifier(seed=42)
        embeddings = np.random.randn(5, 384).astype(np.float32)
        scores = qualifier.bald_scores(embeddings)
        assert np.all(scores <= np.log(2) + 0.01)


class TestRankProfiles:
    def test_rank_profiles_empty(self):
        qualifier = BayesianQualifier(seed=42)
        assert qualifier.rank_profiles([]) == []

    def test_rank_profiles_orders_by_posterior(self, embeddings_db):
        qualifier = BayesianQualifier(seed=42)
        pos_emb = np.ones(384, dtype=np.float32)
        for _ in range(10):
            qualifier.update(pos_emb, 1)

        from linkedin.ml.embeddings import store_embedding
        store_embedding(1, "positive", pos_emb)
        store_embedding(2, "negative", -pos_emb)

        profiles = [
            {"public_identifier": "negative"},
            {"public_identifier": "positive"},
        ]
        ranked = qualifier.rank_profiles(profiles)
        assert ranked[0]["public_identifier"] == "positive"


class TestWarmStart:
    def test_warm_start_matches_sequential(self):
        qualifier1 = BayesianQualifier(seed=42)
        qualifier2 = BayesianQualifier(seed=42)

        rng = np.random.RandomState(99)
        X = rng.randn(20, 384).astype(np.float32)
        y = rng.randint(0, 2, size=20)

        for i in range(20):
            qualifier1.update(X[i], int(y[i]))

        qualifier2.warm_start(X, y)

        np.testing.assert_allclose(qualifier1.mu, qualifier2.mu, atol=1e-8)
        np.testing.assert_allclose(qualifier1.Sigma, qualifier2.Sigma, atol=1e-8)


class TestExplainProfile:
    def test_explain_no_embedding(self, embeddings_db):
        qualifier = BayesianQualifier(seed=42)
        profile = {"public_identifier": "nonexistent"}
        explanation = qualifier.explain_profile(profile)
        assert "no embedding" in explanation.lower()

    def test_explain_with_embedding(self, embeddings_db):
        from linkedin.ml.embeddings import store_embedding

        qualifier = BayesianQualifier(seed=42)
        emb = np.ones(384, dtype=np.float32)
        store_embedding(1, "alice", emb)

        profile = {"public_identifier": "alice"}
        explanation = qualifier.explain_profile(profile)
        assert "posterior predictive" in explanation.lower()
        assert "bald" in explanation.lower()
