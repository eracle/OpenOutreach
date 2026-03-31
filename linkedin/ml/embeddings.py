# linkedin/ml/embeddings.py
"""Fastembed text embedding utilities."""
from __future__ import annotations

import logging

import numpy as np

from linkedin.conf import CAMPAIGN_CONFIG, FASTEMBED_CACHE_DIR

logger = logging.getLogger(__name__)

_model = None


def _get_model():
    """Lazy-load fastembed model singleton."""
    global _model
    if _model is None:
        from fastembed import TextEmbedding

        model_name = CAMPAIGN_CONFIG["embedding_model"]
        logger.debug("Loading embedding model: %s", model_name)
        FASTEMBED_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _model = TextEmbedding(model_name=model_name, cache_dir=str(FASTEMBED_CACHE_DIR))
    return _model


def embed_text(text: str) -> np.ndarray:
    """Embed a single text string → 384-dim numpy array."""
    model = _get_model()
    embeddings = list(model.embed([text]))
    return np.array(embeddings[0], dtype=np.float32)


def embed_texts(texts: list[str]) -> np.ndarray:
    """Embed multiple texts → (N, 384) numpy array."""
    model = _get_model()
    embeddings = list(model.embed(texts))
    return np.array(embeddings, dtype=np.float32)


