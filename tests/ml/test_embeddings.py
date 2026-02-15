# tests/ml/test_embeddings.py
"""Tests for embedding + DuckDB store."""
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
            get_labeled_data,
            store_embedding,
            store_label,
        )

        emb = np.random.randn(384).astype(np.float32)
        store_embedding(1, "alice", emb)
        store_label(1, label=1, reason="Good prospect")

        X, y = get_labeled_data()
        assert len(X) == 1
        assert y[0] == 1

    def test_store_label(self, embeddings_db):
        from linkedin.ml.embeddings import (
            count_labeled,
            store_embedding,
            store_label,
        )

        emb = np.random.randn(384).astype(np.float32)
        store_embedding(1, "alice", emb)

        counts = count_labeled()
        assert counts["total"] == 0

        store_label(1, label=1, reason="Good prospect")

        counts = count_labeled()
        assert counts["positive"] == 1
        assert counts["total"] == 1

    def test_upsert_preserves_existing_data(self, embeddings_db):
        from linkedin.ml.embeddings import store_embedding, store_label, count_labeled

        emb = np.random.randn(384).astype(np.float32)
        store_embedding(1, "alice", emb)
        store_label(1, label=1, reason="Good")

        # Re-store with different embedding — label should be preserved
        emb2 = np.random.randn(384).astype(np.float32)
        store_embedding(1, "alice", emb2)

        counts = count_labeled()
        assert counts["positive"] == 1

    def test_get_all_unlabeled_embeddings(self, embeddings_db):
        from linkedin.ml.embeddings import (
            get_all_unlabeled_embeddings,
            store_embedding,
            store_label,
        )

        emb1 = np.random.randn(384).astype(np.float32)
        emb2 = np.random.randn(384).astype(np.float32)
        emb3 = np.random.randn(384).astype(np.float32)
        store_embedding(1, "alice", emb1)
        store_embedding(2, "bob", emb2)
        store_embedding(3, "carol", emb3)

        # Label one
        store_label(1, label=1, reason="Good")

        results = get_all_unlabeled_embeddings()
        assert len(results) == 2
        ids = {r["public_identifier"] for r in results}
        assert ids == {"bob", "carol"}

    def test_get_labeled_data_empty(self, embeddings_db):
        from linkedin.ml.embeddings import get_labeled_data

        X, y = get_labeled_data()
        assert X.shape == (0, 384)
        assert y.shape == (0,)

    def test_get_embedded_lead_ids(self, embeddings_db):
        from linkedin.ml.embeddings import get_embedded_lead_ids, store_embedding

        emb = np.random.randn(384).astype(np.float32)
        store_embedding(1, "alice", emb)
        store_embedding(2, "bob", emb)

        ids = get_embedded_lead_ids()
        assert ids == {1, 2}
