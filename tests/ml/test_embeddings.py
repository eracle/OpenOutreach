# tests/ml/test_embeddings.py
"""Tests for embedding computation and Lead embedding fields."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch, MagicMock

import numpy as np
import pytest


@pytest.mark.no_embed_mock
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


class TestLeadEmbeddingFields:
    def test_store_and_retrieve(self, db):
        from crm.models import Lead

        emb = np.random.randn(384).astype(np.float32)
        Lead.objects.create(
            pk=1, public_identifier="alice", linkedin_url="https://linkedin.com/in/alice/",
            embedding=emb.tobytes(),
        )

        lead = Lead.objects.get(pk=1)
        np.testing.assert_array_almost_equal(lead.embedding_array, emb)

    def test_embedding_array_setter(self, db):
        from crm.models import Lead

        emb = np.random.randn(384).astype(np.float32)
        lead = Lead(pk=1, public_identifier="alice", linkedin_url="https://linkedin.com/in/alice/")
        lead.embedding_array = emb
        lead.save()

        lead = Lead.objects.get(pk=1)
        np.testing.assert_array_almost_equal(lead.embedding_array, emb)

    def test_embedding_array_none_when_no_embedding(self, db):
        from crm.models import Lead

        lead = Lead.objects.create(
            pk=1, public_identifier="alice", linkedin_url="https://linkedin.com/in/alice/",
        )
        assert lead.embedding_array is None

    def test_get_labeled_arrays_empty(self, fake_session):
        from crm.models import Lead

        campaign = fake_session.campaign
        X, y = Lead.get_labeled_arrays(campaign)
        assert X.shape == (0, 384)
        assert y.shape == (0,)

    def test_get_labeled_arrays_from_deals(self, fake_session):
        """Labels are derived from Deal state + outcome."""
        from crm.models import Deal, Lead, Outcome
        from linkedin.enums import ProfileState

        campaign = fake_session.campaign

        # Create a lead with embedding + QUALIFIED deal → label=1
        emb = np.random.randn(384).astype(np.float32)
        lead = Lead.objects.create(
            linkedin_url="https://linkedin.com/in/alice/",
            public_identifier="alice", embedding=emb.tobytes(),
        )
        Deal.objects.create(
            lead=lead, campaign=campaign, state=ProfileState.QUALIFIED,
        )

        # Create a lead with embedding + FAILED/Disqualified deal → label=0
        emb2 = np.random.randn(384).astype(np.float32)
        lead2 = Lead.objects.create(
            linkedin_url="https://linkedin.com/in/bob/",
            public_identifier="bob", embedding=emb2.tobytes(),
        )
        Deal.objects.create(
            lead=lead2, campaign=campaign, state=ProfileState.FAILED,
            outcome=Outcome.WRONG_FIT,
        )

        X, y = Lead.get_labeled_arrays(campaign)
        assert len(X) == 2
        assert set(y) == {0, 1}

    def test_get_labeled_arrays_skips_operational_failures(self, fake_session):
        """FAILED deals with non-wrong_fit outcome are not training data."""
        from crm.models import Deal, Lead, Outcome
        from linkedin.enums import ProfileState

        campaign = fake_session.campaign

        emb = np.random.randn(384).astype(np.float32)
        lead = Lead.objects.create(
            linkedin_url="https://linkedin.com/in/charlie/",
            public_identifier="charlie", embedding=emb.tobytes(),
        )
        Deal.objects.create(
            lead=lead, campaign=campaign, state=ProfileState.FAILED,
            outcome=Outcome.UNKNOWN,
        )

        X, y = Lead.get_labeled_arrays(campaign)
        assert len(X) == 0

    def test_embedded_lead_ids(self, db):
        from crm.models import Lead

        emb = np.random.randn(384).astype(np.float32)
        Lead.objects.create(
            pk=1, public_identifier="alice",
            linkedin_url="https://linkedin.com/in/alice/",
            embedding=emb.tobytes(),
        )
        Lead.objects.create(
            pk=2, public_identifier="bob",
            linkedin_url="https://linkedin.com/in/bob/",
            embedding=emb.tobytes(),
        )

        ids = set(Lead.objects.filter(embedding__isnull=False).values_list("pk", flat=True))
        assert ids == {1, 2}
