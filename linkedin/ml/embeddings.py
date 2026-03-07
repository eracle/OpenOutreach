# linkedin/ml/embeddings.py
"""Fastembed text embedding utilities."""
from __future__ import annotations

import logging

import numpy as np

from linkedin.conf import CAMPAIGN_CONFIG

logger = logging.getLogger(__name__)

_model = None


def _get_model():
    """Lazy-load fastembed model singleton."""
    global _model
    if _model is None:
        from fastembed import TextEmbedding

        model_name = CAMPAIGN_CONFIG["embedding_model"]
        logger.debug("Loading embedding model: %s", model_name)
        _model = TextEmbedding(model_name=model_name)
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


def embed_profile(lead_id: int, public_id: str, profile_data: dict) -> bool:
    """Build text, compute embedding, and store in DB.

    Returns True if embedding was stored, False on failure.
    """
    from linkedin.ml.profile_text import build_profile_text
    from linkedin.models import ProfileEmbedding

    text = build_profile_text({"profile": profile_data})
    emb = embed_text(text)
    ProfileEmbedding.objects.update_or_create(
        lead_id=lead_id,
        defaults={
            "public_identifier": public_id,
            "embedding": emb.tobytes(),
        },
    )
    return True
