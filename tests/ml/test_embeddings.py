# tests/ml/test_embeddings.py
"""Tests for embedding + DuckDB vector store."""
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


class TestDuckDBOps:
    def test_store_and_retrieve_embedding(self, embeddings_db):
        from linkedin.ml.embeddings import (
            count_labeled,
            get_labeled_data,
            store_embedding,
        )

        emb = np.random.randn(384).astype(np.float32)
        store_embedding(1, "alice", emb, is_seed=True, label=1)

        X, y = get_labeled_data()
        assert len(X) == 1
        assert y[0] == 1

        counts = count_labeled()
        assert counts["positive"] == 1
        assert counts["negative"] == 0
        assert counts["total"] == 1

    def test_store_label(self, embeddings_db):
        from linkedin.ml.embeddings import (
            count_labeled,
            store_embedding,
            store_label,
        )

        emb = np.random.randn(384).astype(np.float32)
        store_embedding(1, "alice", emb, is_seed=False, label=None)

        counts = count_labeled()
        assert counts["total"] == 0

        store_label(1, label=1, reason="Good prospect")

        counts = count_labeled()
        assert counts["positive"] == 1
        assert counts["total"] == 1

    def test_upsert_does_not_overwrite_label(self, embeddings_db):
        from linkedin.ml.embeddings import count_labeled, store_embedding

        emb = np.random.randn(384).astype(np.float32)
        store_embedding(1, "alice", emb, is_seed=True, label=1)
        # Re-store with label=None — should not overwrite
        store_embedding(1, "alice", emb, is_seed=True, label=None)

        counts = count_labeled()
        assert counts["positive"] == 1

    def test_positive_centroid(self, embeddings_db):
        from linkedin.ml.embeddings import get_positive_centroid, store_embedding

        emb1 = np.ones(384, dtype=np.float32)
        emb2 = np.ones(384, dtype=np.float32) * 3
        store_embedding(1, "alice", emb1, is_seed=True, label=1)
        store_embedding(2, "bob", emb2, is_seed=True, label=1)

        centroid = get_positive_centroid()
        assert centroid is not None
        np.testing.assert_allclose(centroid, np.ones(384) * 2, atol=0.01)

    def test_positive_centroid_none_when_no_positives(self, embeddings_db):
        from linkedin.ml.embeddings import get_positive_centroid

        assert get_positive_centroid() is None

    def test_unlabeled_profiles_by_similarity(self, embeddings_db):
        from linkedin.ml.embeddings import (
            get_unlabeled_profiles_by_similarity,
            store_embedding,
        )

        # Create a seed (positive centroid)
        seed_emb = np.ones(384, dtype=np.float32)
        store_embedding(1, "seed", seed_emb, is_seed=True, label=1)

        # Create unlabeled profiles with different similarities
        similar = np.ones(384, dtype=np.float32) * 0.9
        dissimilar = np.ones(384, dtype=np.float32) * -1
        store_embedding(2, "similar", similar, is_seed=False, label=None)
        store_embedding(3, "dissimilar", dissimilar, is_seed=False, label=None)

        results = get_unlabeled_profiles_by_similarity(limit=10)
        assert len(results) == 2
        # Similar should be ranked first
        assert results[0]["public_identifier"] == "similar"

    def test_get_labeled_data_empty(self, embeddings_db):
        from linkedin.ml.embeddings import get_labeled_data

        X, y = get_labeled_data()
        assert X.shape == (0, 384)
        assert y.shape == (0,)
