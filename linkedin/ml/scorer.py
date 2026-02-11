# linkedin/ml/scorer.py
from __future__ import annotations

import logging

from linkedin.conf import DATA_DIR

logger = logging.getLogger(__name__)

ANALYTICS_DB = DATA_DIR / "analytics.duckdb"

FEATURE_COLUMNS = [
    "connection_degree",
    "num_positions",
    "num_educations",
    "has_summary",
    "headline_length",
]


class ProfileScorer:
    def __init__(self, seed: int = 42):
        import numpy as np

        self._rng = np.random.RandomState(seed)
        self._model = None
        self._trained = False

    def train(self) -> bool:
        import duckdb
        import numpy as np
        from sklearn.linear_model import LogisticRegression

        if not ANALYTICS_DB.exists():
            logger.warning("Analytics DB not found at %s — skipping ML training", ANALYTICS_DB)
            return False

        con = duckdb.connect(str(ANALYTICS_DB), read_only=True)
        try:
            cols = ", ".join(FEATURE_COLUMNS)
            df = con.execute(
                f"SELECT {cols}, accepted FROM ml_connection_accepted"
            ).fetchdf()
        except duckdb.CatalogException:
            logger.warning("Table ml_connection_accepted not found — run 'make analytics' first")
            return False
        finally:
            con.close()

        if len(df) < 10:
            logger.warning("Only %d rows in training data — need at least 10", len(df))
            return False

        y = df["accepted"].values
        if len(np.unique(y)) < 2:
            logger.warning("Single class in training data — cannot train")
            return False

        import pandas as pd

        X = df[FEATURE_COLUMNS].apply(pd.to_numeric, errors="coerce").fillna(0).values
        model = LogisticRegression(random_state=42, max_iter=1000)
        model.fit(X, y)

        self._model = model
        self._trained = True
        logger.info("ML model trained on %d rows (%.1f%% accepted)", len(df), y.mean() * 100)
        return True

    def _extract_features(self, profile: dict) -> list:
        p = profile.get("profile", {}) or {}
        return [
            p.get("connection_degree") or 0,
            len(p.get("positions", []) or []),
            len(p.get("educations", []) or []),
            1 if p.get("summary") else 0,
            len(p.get("headline", "") or ""),
        ]

    def score_profiles(self, profiles: list) -> list:
        import numpy as np

        if not self._trained or not profiles:
            return list(profiles)

        scored = []
        for p in profiles:
            features = self._extract_features(p)
            X = np.array([features])
            prob = self._model.predict_proba(X)[0, 1]
            alpha = prob * 10
            beta = (1 - prob) * 10
            sampled = self._rng.beta(max(alpha, 0.01), max(beta, 0.01))
            scored.append((sampled, p))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [p for _, p in scored]

    def explain_profile(self, profile: dict) -> str:
        if not self._trained:
            return "Model not trained — no explanation available"

        features = self._extract_features(profile)
        coefs = self._model.coef_[0]

        contributions = []
        for name, coef, val in zip(FEATURE_COLUMNS, coefs, features):
            contributions.append((name, coef * val, coef, val))

        contributions.sort(key=lambda x: abs(x[1]), reverse=True)

        lines = ["Feature contributions (coef * value):"]
        for name, contrib, coef, val in contributions:
            sign = "+" if contrib >= 0 else ""
            lines.append(f"  {name:25s} {sign}{contrib:8.4f}  (coef={coef:.4f}, val={val})")

        return "\n".join(lines)
