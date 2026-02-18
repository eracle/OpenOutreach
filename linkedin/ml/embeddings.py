# linkedin/ml/embeddings.py
"""Embedding + DuckDB store for profile qualification."""
from __future__ import annotations

import logging
from datetime import datetime

import numpy as np

from linkedin.conf import CAMPAIGN_CONFIG, EMBEDDINGS_DB

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


def _connect(read_only: bool = False):
    """Open a DuckDB connection to the embeddings database."""
    import duckdb

    con = duckdb.connect(str(EMBEDDINGS_DB), read_only=read_only)
    return con


def ensure_embeddings_table():
    """Create the profile_embeddings table if not exists."""
    con = _connect()

    con.execute("""
        CREATE TABLE IF NOT EXISTS profile_embeddings (
            lead_id INTEGER PRIMARY KEY,
            public_identifier VARCHAR NOT NULL,
            embedding FLOAT[384] NOT NULL,
            label INTEGER,
            llm_reason VARCHAR,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            labeled_at TIMESTAMP
        )
    """)

    con.close()
    logger.debug("Embeddings table ensured at %s", EMBEDDINGS_DB)


def store_embedding(
    lead_id: int,
    public_id: str,
    embedding: np.ndarray,
):
    """Upsert a profile embedding into DuckDB."""
    ensure_embeddings_table()
    con = _connect()
    emb_list = embedding.tolist()

    # Check if exists
    existing = con.execute(
        "SELECT lead_id FROM profile_embeddings WHERE lead_id = ?",
        [lead_id],
    ).fetchone()

    if existing:
        con.execute(
            """UPDATE profile_embeddings
               SET embedding = ?, public_identifier = ?
               WHERE lead_id = ?""",
            [emb_list, public_id, lead_id],
        )
    else:
        con.execute(
            """INSERT INTO profile_embeddings
               (lead_id, public_identifier, embedding)
               VALUES (?, ?, ?)""",
            [lead_id, public_id, emb_list],
        )

    con.close()


def store_label(lead_id: int, label: int, reason: str = ""):
    """Update the qualification label for a profile."""
    con = _connect()
    con.execute(
        """UPDATE profile_embeddings
           SET label = ?, llm_reason = ?, labeled_at = ?
           WHERE lead_id = ?""",
        [label, reason, datetime.now(), lead_id],
    )
    con.close()


def get_unlabeled_profiles(limit: int = 10) -> list[dict]:
    """Unlabeled profiles, ordered by creation time (FIFO)."""
    con = _connect(read_only=True)
    rows = con.execute(
        """SELECT lead_id, public_identifier, embedding
           FROM profile_embeddings
           WHERE label IS NULL
           ORDER BY created_at ASC
           LIMIT ?""",
        [limit],
    ).fetchall()
    con.close()

    return [
        {
            "lead_id": row[0],
            "public_identifier": row[1],
            "embedding": np.array(row[2], dtype=np.float32),
        }
        for row in rows
    ]


def get_all_unlabeled_embeddings() -> list[dict]:
    """All unlabeled profiles with embeddings, for BALD ranking."""
    con = _connect(read_only=True)
    rows = con.execute(
        """SELECT lead_id, public_identifier, embedding
           FROM profile_embeddings
           WHERE label IS NULL
           ORDER BY created_at ASC"""
    ).fetchall()
    con.close()

    return [
        {
            "lead_id": row[0],
            "public_identifier": row[1],
            "embedding": np.array(row[2], dtype=np.float32),
        }
        for row in rows
    ]


def get_labeled_data() -> tuple[np.ndarray, np.ndarray]:
    """All labeled embeddings for warm start. Returns (X, y)."""
    con = _connect(read_only=True)
    rows = con.execute(
        """SELECT embedding, label
           FROM profile_embeddings
           WHERE label IS NOT NULL
           ORDER BY labeled_at ASC"""
    ).fetchall()
    con.close()

    if not rows:
        return np.empty((0, 384), dtype=np.float32), np.empty(0, dtype=np.int32)

    X = np.array([row[0] for row in rows], dtype=np.float32)
    y = np.array([row[1] for row in rows], dtype=np.int32)
    return X, y


def embed_profile(lead_id: int, public_id: str, profile_data: dict) -> bool:
    """Build text, compute embedding, and store it for a profile.

    Returns True if embedding was stored, False on failure.
    """
    from linkedin.ml.profile_text import build_profile_text

    text = build_profile_text({"profile": profile_data})
    emb = embed_text(text)
    store_embedding(lead_id, public_id, emb)
    return True


def get_embedded_lead_ids() -> set[int]:
    """Return the set of lead IDs that already have embeddings."""
    con = _connect(read_only=True)
    rows = con.execute("SELECT lead_id FROM profile_embeddings").fetchall()
    con.close()
    return {row[0] for row in rows}


def get_qualification_reason(public_id: str) -> str | None:
    """Return the qualification reason for a profile, or None if not found."""
    con = _connect(read_only=True)
    row = con.execute(
        """SELECT llm_reason FROM profile_embeddings
           WHERE public_identifier = ? AND label IS NOT NULL""",
        [public_id],
    ).fetchone()
    con.close()
    return row[0] if row else None


def count_labeled() -> dict:
    """Count labeled profiles by class."""
    con = _connect(read_only=True)
    rows = con.execute(
        """SELECT label, COUNT(*) as cnt
           FROM profile_embeddings
           WHERE label IS NOT NULL
           GROUP BY label"""
    ).fetchall()
    con.close()

    counts = {"positive": 0, "negative": 0, "total": 0}
    for label, cnt in rows:
        if label == 1:
            counts["positive"] = cnt
        elif label == 0:
            counts["negative"] = cnt
        counts["total"] += cnt

    return counts
