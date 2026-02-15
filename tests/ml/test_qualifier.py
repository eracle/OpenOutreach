# tests/ml/test_qualifier.py
"""Tests for QualificationScorer and LLM qualification."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import numpy as np
import pytest

from linkedin.ml.qualifier import QualificationScorer


class TestQualificationScorerTrain:
    def _store_samples(self, n, label_fn):
        from linkedin.ml.embeddings import store_embedding
        for i in range(n):
            emb = np.random.randn(384).astype(np.float32)
            store_embedding(i, f"user{i}", emb, label=label_fn(i))

    def test_train_returns_false_insufficient_data(self, embeddings_db):
        self._store_samples(5, lambda i: i % 2)
        scorer = QualificationScorer(seed=42)
        assert scorer.train() is False

    def test_train_returns_false_imbalanced(self, embeddings_db):
        self._store_samples(50, lambda i: 1 if i >= 2 else 0)
        scorer = QualificationScorer(seed=42)
        assert scorer.train() is False

    def test_train_succeeds_with_balanced_data(self, embeddings_db):
        self._store_samples(50, lambda i: i % 2)
        scorer = QualificationScorer(seed=42)
        scorer._cfg = {**scorer._cfg, "qualification_n_estimators": 2}
        with patch("sklearn.model_selection.cross_val_score", return_value=np.array([0.9])):
            assert scorer.train() is True
        assert scorer._trained is True
        assert scorer._ensemble is not None


class TestQualificationScorerScoring:
    def test_fifo_when_no_seeds(self, embeddings_db):
        scorer = QualificationScorer(seed=42)
        profiles = [
            {"public_identifier": "a", "meta": {}},
            {"public_identifier": "b", "meta": {}},
        ]
        result = scorer.score_profiles(profiles)
        assert result == profiles

    def test_seeds_ranked_first(self, embeddings_db):
        scorer = QualificationScorer(seed=42)
        profiles = [
            {"public_identifier": "non-seed", "meta": {}},
            {"public_identifier": "seed1", "meta": {"seed": True}},
        ]
        result = scorer.score_profiles(profiles)
        assert result[0]["public_identifier"] == "seed1"

    def test_empty_profiles_returns_empty(self, embeddings_db):
        scorer = QualificationScorer(seed=42)
        assert scorer.score_profiles([]) == []


class TestQualificationScorerPredict:
    def test_predict_distribution_untrained(self):
        scorer = QualificationScorer(seed=42)
        emb = np.random.randn(384).astype(np.float32)
        probs = scorer.predict_distribution(emb)
        assert len(probs) == 0

    def test_predict_untrained_raises(self):
        scorer = QualificationScorer(seed=42)
        emb = np.random.randn(384).astype(np.float32)
        with pytest.raises(RuntimeError, match="untrained"):
            scorer.predict(emb)

    def test_predict_with_trained_model(self):
        scorer = QualificationScorer(seed=42)
        scorer._trained = True

        # Mock ensemble with 3 estimators
        mock_est = MagicMock()
        mock_est.predict_proba.return_value = np.array([[0.3, 0.7]])
        scorer._ensemble = MagicMock()
        scorer._ensemble.estimators_ = [mock_est, mock_est, mock_est]

        test_emb = np.random.randn(384).astype(np.float32)
        probs = scorer.predict_distribution(test_emb)
        assert len(probs) == 3
        assert all(0 <= p <= 1 for p in probs)

        mean, std = scorer.predict(test_emb)
        assert 0 <= mean <= 1
        assert std >= 0


class TestExplainProfile:
    def test_explain_untrained(self, embeddings_db):
        scorer = QualificationScorer(seed=42)
        profile = {"public_identifier": "test"}
        explanation = scorer.explain_profile(profile)
        assert "not trained" in explanation.lower()

    def test_explain_no_embedding(self, embeddings_db):
        scorer = QualificationScorer(seed=42)
        scorer._trained = True

        mock_est = MagicMock()
        mock_est.predict_proba.return_value = np.array([[0.3, 0.7]])
        scorer._ensemble = MagicMock()
        scorer._ensemble.estimators_ = [mock_est] * 3

        profile = {"public_identifier": "nonexistent"}
        explanation = scorer.explain_profile(profile)
        assert "no embedding" in explanation.lower()


class TestNeedsRetrain:
    def test_needs_retrain_false_when_no_new_labels(self, embeddings_db):
        scorer = QualificationScorer(seed=42)
        scorer._labels_at_last_train = 0
        assert scorer.needs_retrain() is False
