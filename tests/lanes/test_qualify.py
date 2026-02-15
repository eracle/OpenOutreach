# tests/lanes/test_qualify.py
"""Tests for the qualification lane."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import numpy as np
import pytest

from linkedin.ml.qualifier import QualificationScorer


class TestQualifyLaneCanExecute:
    def test_can_execute_with_unembedded_profiles(self):
        from linkedin.lanes.qualify import QualifyLane

        scorer = QualificationScorer(seed=42)
        session = MagicMock()

        profiles = [{"public_identifier": "alice"}]
        with (
            patch("linkedin.db.crm_profiles.get_enriched_profiles", return_value=profiles),
            patch("linkedin.ml.embeddings.get_embedded_lead_ids", return_value=set()),
            patch.object(QualifyLane, "_lead_id_for", return_value=1),
        ):
            lane = QualifyLane(session, scorer)
            assert lane.can_execute() is True

    def test_cannot_execute_no_centroid_no_unembedded(self):
        from linkedin.lanes.qualify import QualifyLane

        scorer = QualificationScorer(seed=42)
        session = MagicMock()

        with (
            patch("linkedin.db.crm_profiles.get_enriched_profiles", return_value=[]),
            patch("linkedin.ml.embeddings.get_positive_centroid", return_value=None),
        ):
            lane = QualifyLane(session, scorer)
            assert lane.can_execute() is False

    def test_cannot_execute_no_unlabeled(self):
        from linkedin.lanes.qualify import QualifyLane

        scorer = QualificationScorer(seed=42)
        session = MagicMock()

        centroid = np.ones(384, dtype=np.float32)
        with (
            patch("linkedin.db.crm_profiles.get_enriched_profiles", return_value=[]),
            patch("linkedin.ml.embeddings.get_positive_centroid", return_value=centroid),
            patch("linkedin.ml.embeddings.get_unlabeled_profiles_by_similarity", return_value=[]),
        ):
            lane = QualifyLane(session, scorer)
            assert lane.can_execute() is False

    def test_can_execute_with_centroid_and_unlabeled(self):
        from linkedin.lanes.qualify import QualifyLane

        scorer = QualificationScorer(seed=42)
        session = MagicMock()

        centroid = np.ones(384, dtype=np.float32)
        unlabeled = [{"lead_id": 1, "public_identifier": "alice", "embedding": np.ones(384)}]
        with (
            patch("linkedin.db.crm_profiles.get_enriched_profiles", return_value=[]),
            patch("linkedin.ml.embeddings.get_positive_centroid", return_value=centroid),
            patch("linkedin.ml.embeddings.get_unlabeled_profiles_by_similarity", return_value=unlabeled),
        ):
            lane = QualifyLane(session, scorer)
            assert lane.can_execute() is True


class TestQualifyLaneEmbedding:
    def test_execute_embeds_before_qualifying(self):
        """When unembedded profiles exist, execute embeds one and returns."""
        from linkedin.lanes.qualify import QualifyLane

        scorer = QualificationScorer(seed=42)
        session = MagicMock()

        with (
            patch.object(QualifyLane, "_embed_next_profile", return_value=True) as mock_embed,
            patch.object(QualifyLane, "_qualify_next_profile") as mock_qualify,
        ):
            lane = QualifyLane(session, scorer)
            lane.execute()

            mock_embed.assert_called_once()
            mock_qualify.assert_not_called()

    def test_execute_qualifies_when_no_embedding_needed(self):
        """When all profiles are embedded, execute qualifies instead."""
        from linkedin.lanes.qualify import QualifyLane

        scorer = QualificationScorer(seed=42)
        session = MagicMock()

        with (
            patch.object(QualifyLane, "_embed_next_profile", return_value=False) as mock_embed,
            patch.object(QualifyLane, "_qualify_next_profile") as mock_qualify,
        ):
            lane = QualifyLane(session, scorer)
            lane.execute()

            mock_embed.assert_called_once()
            mock_qualify.assert_called_once()


class TestQualifyLaneAutoDecisions:
    def test_auto_accept_high_confidence(self):
        from linkedin.lanes.qualify import QualifyLane

        scorer = QualificationScorer(seed=42)
        scorer._trained = True
        session = MagicMock()

        candidate = {
            "lead_id": 1,
            "public_identifier": "alice",
            "embedding": np.ones(384, dtype=np.float32),
        }

        with (
            patch.object(QualifyLane, "_embed_next_profile", return_value=False),
            patch("linkedin.ml.embeddings.get_unlabeled_profiles_by_similarity", return_value=[candidate]),
            patch.object(scorer, "predict", return_value=(0.95, 0.05)),
            patch("linkedin.ml.embeddings.store_label") as mock_store,
            patch("linkedin.lanes.qualify.set_profile_state") as mock_set_state,
            patch.object(scorer, "needs_retrain", return_value=False),
        ):
            lane = QualifyLane(session, scorer)
            lane.execute()

            mock_store.assert_called_once()
            call_args = mock_store.call_args
            assert call_args[1]["label"] == 1  # auto-accept
            assert "auto-accept" in call_args[1]["reason"]
            mock_set_state.assert_called_once_with(session, "alice", "qualified")

    def test_auto_reject_low_confidence(self):
        from linkedin.lanes.qualify import QualifyLane

        scorer = QualificationScorer(seed=42)
        scorer._trained = True
        session = MagicMock()

        candidate = {
            "lead_id": 1,
            "public_identifier": "alice",
            "embedding": np.ones(384, dtype=np.float32),
        }

        with (
            patch.object(QualifyLane, "_embed_next_profile", return_value=False),
            patch("linkedin.ml.embeddings.get_unlabeled_profiles_by_similarity", return_value=[candidate]),
            patch.object(scorer, "predict", return_value=(0.10, 0.05)),
            patch("linkedin.ml.embeddings.store_label") as mock_store,
            patch("linkedin.lanes.qualify.set_profile_state") as mock_set_state,
            patch.object(scorer, "needs_retrain", return_value=False),
        ):
            lane = QualifyLane(session, scorer)
            lane.execute()

            mock_store.assert_called_once()
            call_args = mock_store.call_args
            assert call_args[1]["label"] == 0  # auto-reject
            assert "auto-reject" in call_args[1]["reason"]
            mock_set_state.assert_called_once_with(session, "alice", "disqualified")

    def test_llm_query_on_uncertainty(self):
        from linkedin.lanes.qualify import QualifyLane

        scorer = QualificationScorer(seed=42)
        scorer._trained = True
        session = MagicMock()

        candidate = {
            "lead_id": 1,
            "public_identifier": "alice",
            "embedding": np.ones(384, dtype=np.float32),
        }

        with (
            patch.object(QualifyLane, "_embed_next_profile", return_value=False),
            patch("linkedin.ml.embeddings.get_unlabeled_profiles_by_similarity", return_value=[candidate]),
            patch.object(scorer, "predict", return_value=(0.50, 0.20)),
            patch.object(QualifyLane, "_get_profile_text", return_value="engineer at acme"),
            patch("linkedin.ml.qualifier.qualify_profile_llm", return_value=(1, "Good fit")) as mock_llm,
            patch("linkedin.ml.embeddings.store_label") as mock_store,
            patch("linkedin.lanes.qualify.set_profile_state") as mock_set_state,
            patch.object(scorer, "needs_retrain", return_value=False),
        ):
            lane = QualifyLane(session, scorer)
            lane.execute()

            mock_llm.assert_called_once()
            mock_store.assert_called_once()
            mock_set_state.assert_called_once_with(session, "alice", "qualified")

    def test_llm_query_when_no_classifier(self):
        from linkedin.lanes.qualify import QualifyLane

        scorer = QualificationScorer(seed=42)
        session = MagicMock()

        candidate = {
            "lead_id": 1,
            "public_identifier": "alice",
            "embedding": np.ones(384, dtype=np.float32),
        }

        with (
            patch.object(QualifyLane, "_embed_next_profile", return_value=False),
            patch("linkedin.ml.embeddings.get_unlabeled_profiles_by_similarity", return_value=[candidate]),
            patch.object(QualifyLane, "_get_profile_text", return_value="engineer at acme"),
            patch("linkedin.ml.qualifier.qualify_profile_llm", return_value=(0, "Bad fit")) as mock_llm,
            patch("linkedin.ml.embeddings.store_label") as mock_store,
            patch("linkedin.lanes.qualify.set_profile_state") as mock_set_state,
            patch.object(scorer, "needs_retrain", return_value=False),
        ):
            lane = QualifyLane(session, scorer)
            lane.execute()

            mock_llm.assert_called_once()
            mock_set_state.assert_called_once_with(session, "alice", "disqualified")

    def test_retrain_triggered_when_needed(self):
        from linkedin.lanes.qualify import QualifyLane

        scorer = QualificationScorer(seed=42)
        scorer._trained = True
        session = MagicMock()

        candidate = {
            "lead_id": 1,
            "public_identifier": "alice",
            "embedding": np.ones(384, dtype=np.float32),
        }

        with (
            patch.object(QualifyLane, "_embed_next_profile", return_value=False),
            patch("linkedin.ml.embeddings.get_unlabeled_profiles_by_similarity", return_value=[candidate]),
            patch.object(scorer, "predict", return_value=(0.95, 0.05)),
            patch("linkedin.ml.embeddings.store_label"),
            patch("linkedin.lanes.qualify.set_profile_state"),
            patch.object(scorer, "needs_retrain", return_value=True),
            patch.object(scorer, "train") as mock_train,
        ):
            lane = QualifyLane(session, scorer)
            lane.execute()

            mock_train.assert_called_once()
