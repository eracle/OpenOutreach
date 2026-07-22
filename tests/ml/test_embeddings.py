# tests/ml/test_embeddings.py
"""Tests for embedding computation and Lead embedding fields."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import numpy as np
import pytest


@pytest.mark.no_embed_mock
class TestEmbedText:
    def test_embed_text_returns_384_dim(self):
        mock_model = MagicMock()
        mock_model.embed.return_value = [np.random.randn(384).astype(np.float32)]

        with patch("openoutreach.core.ml.embeddings._model", mock_model):
            from openoutreach.core.ml.embeddings import embed_text
            result = embed_text("hello world")

        assert result.shape == (384,)
        assert result.dtype == np.float32

    def test_embed_texts_returns_batch(self):
        mock_model = MagicMock()
        mock_model.embed.return_value = [
            np.random.randn(384).astype(np.float32),
            np.random.randn(384).astype(np.float32),
        ]

        with patch("openoutreach.core.ml.embeddings._model", mock_model):
            from openoutreach.core.ml.embeddings import embed_texts
            result = embed_texts(["hello", "world"])

        assert result.shape == (2, 384)


class TestLeadEmbeddingFields:
    def test_store_and_retrieve(self, db):
        from openoutreach.crm.models import Lead

        emb = np.random.randn(384).astype(np.float32)
        Lead.objects.create(
            pk=1, profile_url="https://linkedin.com/in/alice/",
            embedding=emb.tobytes(),
        )

        lead = Lead.objects.get(pk=1)
        np.testing.assert_array_almost_equal(lead.embedding_array, emb)

    def test_embedding_array_setter(self, db):
        from openoutreach.crm.models import Lead

        emb = np.random.randn(384).astype(np.float32)
        lead = Lead(pk=1, profile_url="https://linkedin.com/in/alice/")
        lead.embedding_array = emb
        lead.save()

        lead = Lead.objects.get(pk=1)
        np.testing.assert_array_almost_equal(lead.embedding_array, emb)

    def test_embedding_array_none_when_no_embedding(self, db):
        from openoutreach.crm.models import Lead

        lead = Lead.objects.create(
            pk=1, profile_url="https://linkedin.com/in/alice/",
        )
        assert lead.embedding_array is None

    def test_get_labeled_arrays_empty(self, fake_session):
        from openoutreach.crm.models import Lead

        campaign = fake_session.campaign
        X, y = Lead.get_labeled_arrays(campaign)
        assert X.shape == (0, 384)
        assert y.shape == (0,)

    def test_get_labeled_arrays_from_deals(self, fake_session):
        """Labels are derived from Deal state + outcome."""
        from openoutreach.crm.models import Deal, Lead, Outcome, DealState

        campaign = fake_session.campaign

        # Create a lead with embedding + QUALIFIED deal → label=1
        emb = np.random.randn(384).astype(np.float32)
        lead = Lead.objects.create(
            profile_url="https://linkedin.com/in/alice/", embedding=emb.tobytes(),
        )
        Deal.objects.create(
            lead=lead, campaign=campaign, state=DealState.QUALIFIED,
        )

        # Create a lead with embedding + FAILED/Disqualified deal → label=0
        emb2 = np.random.randn(384).astype(np.float32)
        lead2 = Lead.objects.create(
            profile_url="https://linkedin.com/in/bob/", embedding=emb2.tobytes(),
        )
        Deal.objects.create(
            lead=lead2, campaign=campaign, state=DealState.FAILED,
            outcome=Outcome.WRONG_FIT,
        )

        X, y = Lead.get_labeled_arrays(campaign)
        assert len(X) == 2
        assert set(y) == {0, 1}

    def test_get_labeled_arrays_keeps_no_email_miss_positive(self, fake_session):
        """A NO_EMAIL_BETTERCONTACT miss is a fit positive (label=1), not skipped —
        the LLM qualified it; only enrichment failed."""
        from openoutreach.crm.models import Deal, Lead, DealState

        campaign = fake_session.campaign
        emb = np.random.randn(384).astype(np.float32)
        lead = Lead.objects.create(
            profile_url="https://linkedin.com/in/dana/", embedding=emb.tobytes(),
        )
        Deal.objects.create(
            lead=lead, campaign=campaign,
            state=DealState.NO_EMAIL_BETTERCONTACT,
        )

        X, y = Lead.get_labeled_arrays(campaign)
        assert len(X) == 1
        assert list(y) == [1]

    def test_get_labeled_arrays_skips_operational_failures(self, fake_session):
        """FAILED deals with non-wrong_fit outcome are not training data."""
        from openoutreach.crm.models import Deal, Lead, Outcome, DealState

        campaign = fake_session.campaign

        emb = np.random.randn(384).astype(np.float32)
        lead = Lead.objects.create(
            profile_url="https://linkedin.com/in/charlie/", embedding=emb.tobytes(),
        )
        Deal.objects.create(
            lead=lead, campaign=campaign, state=DealState.FAILED,
            outcome=Outcome.UNKNOWN,
        )

        X, y = Lead.get_labeled_arrays(campaign)
        assert len(X) == 0

    def test_embedded_lead_ids(self, db):
        from openoutreach.crm.models import Lead

        emb = np.random.randn(384).astype(np.float32)
        Lead.objects.create(
            pk=1, profile_url="https://linkedin.com/in/alice/",
            embedding=emb.tobytes(),
        )
        Lead.objects.create(
            pk=2, profile_url="https://linkedin.com/in/bob/",
            embedding=emb.tobytes(),
        )

        ids = set(Lead.objects.filter(embedding__isnull=False).values_list("pk", flat=True))
        assert ids == {1, 2}
