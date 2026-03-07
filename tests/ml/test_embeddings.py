# tests/ml/test_embeddings.py
"""Tests for embedding computation and ProfileEmbedding model."""
from __future__ import annotations

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

    def test_label_and_count(self, embeddings_db):
        from django.utils import timezone
        from linkedin.models import ProfileEmbedding

        emb = np.random.randn(384).astype(np.float32)
        ProfileEmbedding.objects.create(
            lead_id=1, public_identifier="alice", embedding=emb.tobytes(),
        )

        assert ProfileEmbedding.objects.filter(label__isnull=False).count() == 0

        ProfileEmbedding.objects.filter(lead_id=1).update(
            label=1, llm_reason="Good prospect", labeled_at=timezone.now(),
        )

        assert ProfileEmbedding.objects.filter(label=1).count() == 1

    def test_upsert_preserves_label(self, embeddings_db):
        from django.utils import timezone
        from linkedin.models import ProfileEmbedding

        emb = np.random.randn(384).astype(np.float32)
        ProfileEmbedding.objects.create(
            lead_id=1, public_identifier="alice", embedding=emb.tobytes(),
        )
        ProfileEmbedding.objects.filter(lead_id=1).update(
            label=1, llm_reason="Good", labeled_at=timezone.now(),
        )

        # Re-store with different embedding — label should be preserved
        emb2 = np.random.randn(384).astype(np.float32)
        ProfileEmbedding.objects.update_or_create(
            lead_id=1,
            defaults={"public_identifier": "alice", "embedding": emb2.tobytes()},
        )

        row = ProfileEmbedding.objects.get(lead_id=1)
        assert row.label == 1

    def test_unlabeled_filter(self, embeddings_db):
        from django.utils import timezone
        from linkedin.models import ProfileEmbedding

        for i, name in enumerate(["alice", "bob", "carol"], start=1):
            emb = np.random.randn(384).astype(np.float32)
            ProfileEmbedding.objects.create(
                lead_id=i, public_identifier=name, embedding=emb.tobytes(),
            )

        ProfileEmbedding.objects.filter(lead_id=1).update(
            label=1, llm_reason="Good", labeled_at=timezone.now(),
        )

        unlabeled = ProfileEmbedding.objects.filter(label__isnull=True)
        assert unlabeled.count() == 2
        ids = set(unlabeled.values_list("public_identifier", flat=True))
        assert ids == {"bob", "carol"}

    def test_get_labeled_arrays_empty(self, embeddings_db):
        from linkedin.models import ProfileEmbedding

        X, y = ProfileEmbedding.get_labeled_arrays()
        assert X.shape == (0, 384)
        assert y.shape == (0,)

    def test_get_labeled_arrays(self, embeddings_db):
        from django.utils import timezone
        from linkedin.models import ProfileEmbedding

        emb = np.random.randn(384).astype(np.float32)
        ProfileEmbedding.objects.create(
            lead_id=1, public_identifier="alice", embedding=emb.tobytes(),
            label=1, llm_reason="Good", labeled_at=timezone.now(),
        )

        X, y = ProfileEmbedding.get_labeled_arrays()
        assert len(X) == 1
        assert y[0] == 1

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
