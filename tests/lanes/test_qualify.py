# tests/lanes/test_qualify.py
"""Tests for the qualification lane with entropy-based active learning."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import numpy as np
import pytest

from linkedin.ml.qualifier import BayesianQualifier


def _make_trained_qualifier(seed=42):
    """Create a qualifier with both classes so n_obs > 0 and GPC can fit."""
    qualifier = BayesianQualifier(seed=seed)
    rng = np.random.RandomState(seed)
    for _ in range(5):
        qualifier.update(rng.randn(384).astype(np.float32) + 1.0, 1)
        qualifier.update(rng.randn(384).astype(np.float32) - 1.0, 0)
    return qualifier


def _create_embedding(lead_id, public_id, label=None):
    """Helper to create a ProfileEmbedding in the test DB."""
    from linkedin.models import ProfileEmbedding

    emb = np.ones(384, dtype=np.float32)
    return ProfileEmbedding.objects.create(
        lead_id=lead_id,
        public_identifier=public_id,
        embedding=emb.tobytes(),
        label=label,
    )


class TestQualifyLaneCanExecute:
    def test_can_execute_with_unembedded_leads(self, embeddings_db):
        from linkedin.lanes.qualify import QualifyLane

        qualifier = BayesianQualifier(seed=42)
        session = MagicMock()

        leads = [{"public_identifier": "alice", "lead_id": 1}]
        with patch("linkedin.db.crm_profiles.get_leads_for_qualification", return_value=leads):
            lane = QualifyLane(session, qualifier)
            assert lane.can_execute() is True

    def test_cannot_execute_no_unlabeled(self, embeddings_db):
        from linkedin.lanes.qualify import QualifyLane

        qualifier = BayesianQualifier(seed=42)
        session = MagicMock()

        with patch("linkedin.db.crm_profiles.get_leads_for_qualification", return_value=[]):
            lane = QualifyLane(session, qualifier)
            assert lane.can_execute() is False

    def test_can_execute_with_unlabeled(self, embeddings_db):
        from linkedin.lanes.qualify import QualifyLane

        qualifier = BayesianQualifier(seed=42)
        session = MagicMock()

        _create_embedding(1, "alice")  # unlabeled

        with patch("linkedin.db.crm_profiles.get_leads_for_qualification", return_value=[]):
            lane = QualifyLane(session, qualifier)
            assert lane.can_execute() is True


class TestQualifyLaneEmbedding:
    def test_execute_embeds_before_qualifying(self):
        """When unembedded profiles exist, execute embeds one and returns."""
        from linkedin.lanes.qualify import QualifyLane

        qualifier = BayesianQualifier(seed=42)
        session = MagicMock()

        with (
            patch.object(QualifyLane, "_embed_next_profile", return_value=True) as mock_embed,
            patch.object(QualifyLane, "_qualify_next_profile") as mock_qualify,
        ):
            lane = QualifyLane(session, qualifier)
            lane.execute()

            mock_embed.assert_called_once()
            mock_qualify.assert_not_called()

    def test_execute_qualifies_when_no_embedding_needed(self):
        """When all profiles are embedded, execute qualifies instead."""
        from linkedin.lanes.qualify import QualifyLane

        qualifier = BayesianQualifier(seed=42)
        session = MagicMock()

        with (
            patch.object(QualifyLane, "_embed_next_profile", return_value=False) as mock_embed,
            patch.object(QualifyLane, "_qualify_next_profile") as mock_qualify,
        ):
            lane = QualifyLane(session, qualifier)
            lane.execute()

            mock_embed.assert_called_once()
            mock_qualify.assert_called_once()


class TestQualifyLaneAutoDecisions:
    def test_auto_accept_low_entropy(self, embeddings_db):
        """Low entropy + prob > 0.5 -> auto-accept, promotes lead to contact."""
        from linkedin.lanes.qualify import QualifyLane
        from linkedin.models import ProfileEmbedding

        qualifier = _make_trained_qualifier()
        session = MagicMock()

        _create_embedding(1, "alice")

        with (
            patch.object(QualifyLane, "_embed_next_profile", return_value=False),
            patch.object(qualifier, "predict", return_value=(0.95, 0.01, 0.01)),
            patch.object(qualifier, "update") as mock_update,
            patch("linkedin.db.crm_profiles.promote_lead_to_contact") as mock_promote,
            patch("linkedin.db.crm_profiles.disqualify_lead") as mock_disqualify,
        ):
            lane = QualifyLane(session, qualifier)
            lane.execute()

            row = ProfileEmbedding.objects.get(lead_id=1)
            assert row.label == 1
            assert "auto-accept" in row.llm_reason.lower()
            mock_promote.assert_called_once_with(session, "alice")
            mock_disqualify.assert_not_called()
            mock_update.assert_called_once()

    def test_auto_reject_low_entropy(self, embeddings_db):
        """Low entropy + prob < 0.5 -> auto-reject, disqualifies lead."""
        from linkedin.lanes.qualify import QualifyLane
        from linkedin.models import ProfileEmbedding

        qualifier = _make_trained_qualifier()
        session = MagicMock()

        _create_embedding(1, "alice")

        with (
            patch.object(QualifyLane, "_embed_next_profile", return_value=False),
            patch.object(qualifier, "predict", return_value=(0.05, 0.01, 0.01)),
            patch.object(qualifier, "update") as mock_update,
            patch("linkedin.db.crm_profiles.promote_lead_to_contact") as mock_promote,
            patch("linkedin.db.crm_profiles.disqualify_lead") as mock_disqualify,
        ):
            lane = QualifyLane(session, qualifier)
            lane.execute()

            row = ProfileEmbedding.objects.get(lead_id=1)
            assert row.label == 0
            assert "auto-reject" in row.llm_reason.lower()
            mock_disqualify.assert_called_once()
            mock_promote.assert_not_called()
            mock_update.assert_called_once()

    def test_llm_query_on_high_entropy(self, embeddings_db):
        """High entropy -> query LLM."""
        from linkedin.lanes.qualify import QualifyLane
        from linkedin.models import ProfileEmbedding

        qualifier = _make_trained_qualifier()
        session = MagicMock()

        _create_embedding(1, "alice")

        with (
            patch.object(QualifyLane, "_embed_next_profile", return_value=False),
            # prob=0.50, entropy=0.693 (max), well above threshold
            patch.object(qualifier, "predict", return_value=(0.50, 0.693, 0.5)),
            patch.object(QualifyLane, "_get_profile_text", return_value="engineer at acme"),
            patch("linkedin.ml.qualifier.qualify_with_llm", return_value=(1, "Good fit")) as mock_llm,
            patch.object(qualifier, "update") as mock_update,
            patch("linkedin.db.crm_profiles.promote_lead_to_contact") as mock_promote,
        ):
            lane = QualifyLane(session, qualifier)
            lane.execute()

            mock_llm.assert_called_once()
            row = ProfileEmbedding.objects.get(lead_id=1)
            assert row.label == 1
            mock_promote.assert_called_once_with(session, "alice")
            mock_update.assert_called_once()

    def test_llm_query_on_cold_start(self, embeddings_db):
        """Cold start (predict returns None) -> always query LLM."""
        from linkedin.lanes.qualify import QualifyLane
        from linkedin.models import ProfileEmbedding

        qualifier = BayesianQualifier(seed=42)
        assert qualifier.n_obs == 0
        session = MagicMock()

        _create_embedding(1, "alice")

        with (
            patch.object(QualifyLane, "_embed_next_profile", return_value=False),
            patch.object(QualifyLane, "_get_profile_text", return_value="engineer at acme"),
            patch("linkedin.ml.qualifier.qualify_with_llm", return_value=(0, "Bad fit")) as mock_llm,
            patch.object(qualifier, "update") as mock_update,
            patch("linkedin.db.crm_profiles.disqualify_lead") as mock_disqualify,
        ):
            lane = QualifyLane(session, qualifier)
            lane.execute()

            mock_llm.assert_called_once()
            mock_disqualify.assert_called_once()
            mock_update.assert_called_once()

    def test_auto_disqualify_on_promote_failure(self, embeddings_db):
        """If promote_lead_to_contact raises ValueError (no Company), auto-disqualify."""
        from linkedin.lanes.qualify import QualifyLane

        qualifier = _make_trained_qualifier()
        session = MagicMock()

        _create_embedding(1, "alice")

        with (
            patch.object(QualifyLane, "_embed_next_profile", return_value=False),
            patch.object(qualifier, "predict", return_value=(0.95, 0.01, 0.01)),
            patch.object(qualifier, "update"),
            patch("linkedin.db.crm_profiles.promote_lead_to_contact",
                  side_effect=ValueError("Lead alice has no Company")),
            patch("linkedin.db.crm_profiles.disqualify_lead") as mock_disqualify,
        ):
            lane = QualifyLane(session, qualifier)
            lane.execute()

            mock_disqualify.assert_called_once()

    def test_record_decision_calls_update(self, embeddings_db):
        """After recording a decision, qualifier.update() is called with the correct args."""
        from linkedin.lanes.qualify import QualifyLane

        qualifier = _make_trained_qualifier()
        session = MagicMock()

        emb = np.ones(384, dtype=np.float32)
        _create_embedding(1, "alice")

        with (
            patch.object(QualifyLane, "_embed_next_profile", return_value=False),
            patch.object(qualifier, "predict", return_value=(0.95, 0.01, 0.01)),
            patch.object(qualifier, "update") as mock_update,
            patch("linkedin.db.crm_profiles.promote_lead_to_contact"),
        ):
            lane = QualifyLane(session, qualifier)
            lane.execute()

            mock_update.assert_called_once()
            call_args = mock_update.call_args[0]
            np.testing.assert_array_equal(call_args[0], emb)
            assert call_args[1] == 1  # label
