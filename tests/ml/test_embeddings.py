# tests/ml/test_embeddings.py
"""Tests for embedding computation and ProfileEmbedding model."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch, MagicMock

import numpy as np
import pytest


class TestEmbedText:
    def test_embed_text_returns_384_dim(self):
        mock_model = MagicMock()
        mock_model.embed.return_value = [np.random.randn(384).astype(np.float32)]

        with patch("linkedin.ml.embeddings._model", mock_model):
            from linkedin.ml.embeddings import embed_text
            result = embed_text("hello world")

        assert result.shape == (384,)
        assert result.dtype == np.float32

    def test_embed_texts_returns_batch(self):
        mock_model = MagicMock()
        mock_model.embed.return_value = [
            np.random.randn(384).astype(np.float32),
            np.random.randn(384).astype(np.float32),
        ]

        with patch("linkedin.ml.embeddings._model", mock_model):
            from linkedin.ml.embeddings import embed_texts
            result = embed_texts(["hello", "world"])

        assert result.shape == (2, 384)


class TestProfileEmbeddingModel:
    def test_store_and_retrieve(self, embeddings_db):
        from linkedin.models import ProfileEmbedding

        emb = np.random.randn(384).astype(np.float32)
        ProfileEmbedding.objects.create(
            lead_id=1, public_identifier="alice", embedding=emb.tobytes(),
        )

        row = ProfileEmbedding.objects.get(lead_id=1)
        np.testing.assert_array_almost_equal(row.embedding_array, emb)

    def test_embedding_array_setter(self, embeddings_db):
        from linkedin.models import ProfileEmbedding

        emb = np.random.randn(384).astype(np.float32)
        obj = ProfileEmbedding(lead_id=1, public_identifier="alice")
        obj.embedding_array = emb
        obj.save()

        row = ProfileEmbedding.objects.get(lead_id=1)
        np.testing.assert_array_almost_equal(row.embedding_array, emb)

    def test_get_labeled_arrays_empty(self, fake_session):
        from linkedin.models import ProfileEmbedding

        dept = fake_session.campaign.department
        X, y = ProfileEmbedding.get_labeled_arrays(dept)
        assert X.shape == (0, 384)
        assert y.shape == (0,)

    def test_get_labeled_arrays_from_deals(self, fake_session):
        """Labels are derived from Deal stage + closing_reason, not ProfileEmbedding fields."""
        from crm.models import Deal, Lead, Stage, ClosingReason
        from linkedin.db._helpers import _make_ticket
        from linkedin.models import ProfileEmbedding

        dept = fake_session.campaign.department
        user = fake_session.django_user

        # Create a lead + embedding + QUALIFIED deal → label=1
        lead = Lead.objects.create(website="https://linkedin.com/in/alice", owner=user, department=dept)
        emb = np.random.randn(384).astype(np.float32)
        ProfileEmbedding.objects.create(lead_id=lead.pk, public_identifier="alice", embedding=emb.tobytes())
        qualified_stage = Stage.objects.get(name="Qualified", department=dept)
        Deal.objects.create(
            name="test", lead=lead, stage=qualified_stage,
            owner=user, department=dept, next_step_date=date.today(),
            ticket=_make_ticket(),
        )

        # Create a lead + embedding + FAILED/Disqualified deal → label=0
        lead2 = Lead.objects.create(website="https://linkedin.com/in/bob", owner=user, department=dept)
        emb2 = np.random.randn(384).astype(np.float32)
        ProfileEmbedding.objects.create(lead_id=lead2.pk, public_identifier="bob", embedding=emb2.tobytes())
        failed_stage = Stage.objects.get(name="Failed", department=dept)
        closing = ClosingReason.objects.get(name="Disqualified", department=dept)
        Deal.objects.create(
            name="test2", lead=lead2, stage=failed_stage,
            owner=user, department=dept, closing_reason=closing, active=False,
            next_step_date=date.today(), ticket=_make_ticket(),
        )

        X, y = ProfileEmbedding.get_labeled_arrays(dept)
        assert len(X) == 2
        assert set(y) == {0, 1}

    def test_get_labeled_arrays_skips_operational_failures(self, fake_session):
        """FAILED deals with non-Disqualified closing reason are not training data."""
        from crm.models import Deal, Lead, Stage, ClosingReason
        from linkedin.db._helpers import _make_ticket
        from linkedin.models import ProfileEmbedding

        dept = fake_session.campaign.department
        user = fake_session.django_user

        lead = Lead.objects.create(website="https://linkedin.com/in/charlie", owner=user, department=dept)
        emb = np.random.randn(384).astype(np.float32)
        ProfileEmbedding.objects.create(lead_id=lead.pk, public_identifier="charlie", embedding=emb.tobytes())
        failed_stage = Stage.objects.get(name="Failed", department=dept)
        closing = ClosingReason.objects.get(name="Failed", department=dept)
        Deal.objects.create(
            name="test", lead=lead, stage=failed_stage,
            owner=user, department=dept, closing_reason=closing, active=False,
            next_step_date=date.today(), ticket=_make_ticket(),
        )

        X, y = ProfileEmbedding.get_labeled_arrays(dept)
        assert len(X) == 0

    def test_embedded_lead_ids(self, embeddings_db):
        from linkedin.models import ProfileEmbedding

        emb = np.random.randn(384).astype(np.float32)
        ProfileEmbedding.objects.create(
            lead_id=1, public_identifier="alice", embedding=emb.tobytes(),
        )
        ProfileEmbedding.objects.create(
            lead_id=2, public_identifier="bob", embedding=emb.tobytes(),
        )

        ids = set(ProfileEmbedding.objects.values_list("lead_id", flat=True))
        assert ids == {1, 2}
