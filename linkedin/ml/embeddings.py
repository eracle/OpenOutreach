# linkedin/ml/embeddings.py
"""Embedding + DuckDB vector store for profile qualification."""
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
        logger.info("Loading embedding model: %s", model_name)
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

    return duckdb.connect(str(EMBEDDINGS_DB), read_only=read_only)


def ensure_embeddings_table():
    """Create the profile_embeddings table + HNSW index if not exists."""
    con = _connect()
    try:
        con.execute("INSTALL vss; LOAD vss;")
    except Exception:
        # Already installed/loaded
        try:
            con.execute("LOAD vss;")
        except Exception:
            pass

    con.execute("""
        CREATE TABLE IF NOT EXISTS profile_embeddings (
            lead_id INTEGER PRIMARY KEY,
            public_identifier VARCHAR NOT NULL,
            embedding FLOAT[384] NOT NULL,
            is_seed BOOLEAN DEFAULT FALSE,
            label INTEGER,
            llm_reason VARCHAR,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            labeled_at TIMESTAMP
        )
    """)

    # Create HNSW index for cosine similarity search
    try:
        con.execute("""
            CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw
            ON profile_embeddings
            USING HNSW (embedding)
            WITH (metric = 'cosine')
        """)
    except Exception:
        # Index may already exist or vss extension may not support it
        pass

    con.close()
    logger.debug("Embeddings table ensured at %s", EMBEDDINGS_DB)


def store_embedding(
    lead_id: int,
    public_id: str,
    embedding: np.ndarray,
    is_seed: bool = False,
    label: int | None = None,
):
    """Upsert a profile embedding into DuckDB."""
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
               SET embedding = ?, is_seed = ?, public_identifier = ?
               WHERE lead_id = ?""",
            [emb_list, is_seed, public_id, lead_id],
        )
        # Update label only if provided (don't overwrite existing label)
        if label is not None:
            con.execute(
                """UPDATE profile_embeddings
                   SET label = ?, labeled_at = ?
                   WHERE lead_id = ? AND label IS NULL""",
                [label, datetime.now(), lead_id],
            )
    else:
        con.execute(
            """INSERT INTO profile_embeddings
               (lead_id, public_identifier, embedding, is_seed, label, labeled_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [lead_id, public_id, emb_list, is_seed, label,
             datetime.now() if label is not None else None],
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


def get_positive_centroid() -> np.ndarray | None:
    """Average embedding of all positive profiles (seeds + LLM-accepted).

    Returns None if no positive profiles exist or DB doesn't exist yet.
    """
    con = _connect(read_only=True)
    rows = con.execute(
        "SELECT embedding FROM profile_embeddings WHERE label = 1"
    ).fetchall()
    con.close()

    if not rows:
        return None

    embeddings = np.array([row[0] for row in rows], dtype=np.float32)
    return embeddings.mean(axis=0)


def get_unlabeled_profiles_by_similarity(limit: int = 10) -> list[dict]:
    """Unlabeled non-seed profiles ranked by cosine similarity to positive centroid."""
    centroid = get_positive_centroid()
    if centroid is None:
        return []

    con = _connect(read_only=True)
    rows = con.execute(
        """SELECT lead_id, public_identifier, embedding
           FROM profile_embeddings
           WHERE is_seed = FALSE AND label IS NULL
           ORDER BY array_cosine_similarity(embedding, ?::FLOAT[384]) DESC
           LIMIT ?""",
        [centroid.tolist(), limit],
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
    """All labeled embeddings for training. Returns (X, y)."""
    con = _connect(read_only=True)
    rows = con.execute(
        """SELECT embedding, label
           FROM profile_embeddings
           WHERE label IS NOT NULL"""
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
    from crm.models import Deal, Lead

    from linkedin.db.crm_profiles import _parse_next_step
    from linkedin.ml.profile_text import build_profile_text

    try:
        text = build_profile_text({"profile": profile_data})
        emb = embed_text(text)

        lead = Lead.objects.filter(pk=lead_id).first()
        if not lead:
            return False

        deal = Deal.objects.filter(lead=lead).first()
        is_seed = _parse_next_step(deal).get("seed", False) if deal else False
        store_embedding(
            lead_id, public_id, emb,
            is_seed=is_seed,
            label=1 if is_seed else None,
        )
        return True
    except Exception:
        logger.warning("Failed to embed %s (non-fatal)", public_id, exc_info=True)
        return False


def get_embedded_lead_ids() -> set[int]:
    """Return the set of lead IDs that already have embeddings."""
    con = _connect(read_only=True)
    rows = con.execute("SELECT lead_id FROM profile_embeddings").fetchall()
    con.close()
    return {row[0] for row in rows}


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
