# linkedin/ml/scorer.py
from __future__ import annotations

import logging
from datetime import date

from linkedin.conf import DATA_DIR, KEYWORDS_FILE
from linkedin.ml.keywords import (
    build_profile_text,
    cold_start_score,
    compute_keyword_features,
    keyword_feature_names,
    load_keywords,
)

logger = logging.getLogger(__name__)

ANALYTICS_DB = DATA_DIR / "analytics.duckdb"

# Mechanical features matching the mart column names exactly.
# Kept minimal to avoid overshadowing keyword-based features.
MECHANICAL_FEATURES = [
    "connection_degree",
    "is_currently_employed",
    "years_experience",
]


class ProfileScorer:
    def __init__(self, seed: int = 42, keywords_path=None):
        import numpy as np

        self._rng = np.random.RandomState(seed)
        self._model = None
        self._trained = False
        self._feature_means = None
        self._keywords = load_keywords(keywords_path or KEYWORDS_FILE)
        self._kw_names = keyword_feature_names(self._keywords)
        self._all_feature_names = list(MECHANICAL_FEATURES) + self._kw_names
        self.has_keywords = any(
            len(self._keywords[cat]) > 0
            for cat in ("positive", "negative", "exploratory")
        )

    def train(self) -> bool:
        import duckdb
        import numpy as np
        import pandas as pd
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.model_selection import StratifiedKFold, cross_val_score

        if not ANALYTICS_DB.exists():
            logger.warning("Analytics DB not found at %s — skipping ML training", ANALYTICS_DB)
            return False

        con = duckdb.connect(str(ANALYTICS_DB), read_only=True)
        try:
            cols = ", ".join(MECHANICAL_FEATURES)
            df = con.execute(
                f"SELECT {cols}, profile_text, accepted FROM ml_connection_accepted"
            ).fetchdf()
        except duckdb.CatalogException:
            logger.warning("Table ml_connection_accepted not found — run 'make analytics' first")
            return False
        except duckdb.BinderException as e:
            logger.warning("Analytics DB schema mismatch: %s", e)
            raise
        finally:
            con.close()

        if len(df) < 10:
            logger.debug("Only %d rows in training data — need at least 10", len(df))
            return False

        y = df["accepted"].values
        if len(np.unique(y)) < 2:
            logger.warning("Single class in training data — cannot train")
            return False

        X_mech = df[MECHANICAL_FEATURES].apply(pd.to_numeric, errors="coerce").fillna(0).values

        # Compute keyword features from profile_text
        if self.has_keywords:
            kw_rows = []
            for text in df["profile_text"].fillna(""):
                kw_rows.append(compute_keyword_features(text, self._keywords))
            X_kw = np.array(kw_rows, dtype=float)
            X = np.hstack([X_mech, X_kw])
        else:
            X = X_mech

        model = HistGradientBoostingClassifier(
            max_iter=200,
            max_depth=4,
            min_samples_leaf=5,
            l2_regularization=1.0,
            random_state=42,
        )

        # Cross-validation to detect overfitting
        n_splits = min(5, max(2, len(df) // 10))
        cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
        scores = cross_val_score(model, X, y, cv=cv, scoring="roc_auc")
        logger.info(
            "CV ROC-AUC (%d-fold): %.3f ± %.3f",
            n_splits, scores.mean(), scores.std(),
        )

        # Fit on full data for production use
        model.fit(X, y)

        self._model = model
        self._feature_means = X.mean(axis=0)
        self._trained = True
        n_features = X.shape[1]
        logger.info(
            "ML model trained on %d rows, %d features (%.1f%% accepted)",
            len(df), n_features, y.mean() * 100,
        )
        return True

    def _extract_mechanical_features(self, profile: dict) -> list:
        """Compute mechanical features from in-memory profile dict."""
        p = profile.get("profile", {}) or {}
        positions = p.get("positions", []) or []

        now_frac = date.today().year + date.today().month / 12.0
        is_currently_employed = 0
        start_fracs = []
        end_fracs = []

        for pos in positions:
            if pos.get("end_year") is None:
                is_currently_employed = 1

            start_year = pos.get("start_year")
            if start_year is not None:
                start_month = pos.get("start_month") or 1
                end_year = pos.get("end_year")
                end_month = pos.get("end_month") or 1
                start_fracs.append(start_year + start_month / 12.0)
                end_fracs.append(
                    (end_year + end_month / 12.0) if end_year else now_frac
                )

        years_experience = (max(end_fracs) - min(start_fracs)) if start_fracs else 0.0

        return [
            p.get("connection_degree") or 0,
            is_currently_employed,
            years_experience,
        ]

    def _extract_features(self, profile: dict) -> list:
        """Extract all features (mechanical + keyword) from profile."""
        features = self._extract_mechanical_features(profile)
        if self.has_keywords:
            text = build_profile_text(profile)
            features.extend(compute_keyword_features(text, self._keywords))
        return features

    def score_profiles(self, profiles: list) -> list:
        import numpy as np

        if not profiles:
            return list(profiles)

        # Untrained: cold-start Thompson Sampling or FIFO
        if not self._trained:
            if self.has_keywords:
                raw_scores = []
                for p in profiles:
                    text = build_profile_text(p)
                    raw_scores.append(cold_start_score(text, self._keywords))

                lo, hi = min(raw_scores), max(raw_scores)
                scored = []
                for raw, p in zip(raw_scores, profiles):
                    # Normalize to (0, 1); all-equal → 0.5 → pure exploration
                    prob = (raw - lo) / (hi - lo) if hi > lo else 0.5
                    # Lower confidence (k=3) than trained model (k=10)
                    # to encourage exploration during cold start
                    alpha = prob * 3
                    beta_param = (1 - prob) * 3
                    sampled = self._rng.beta(max(alpha, 0.01), max(beta_param, 0.01))
                    scored.append((sampled, p))
                scored.sort(key=lambda x: x[0], reverse=True)
                return [p for _, p in scored]
            return list(profiles)

        # Trained: Thompson Sampling
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
            if self.has_keywords:
                text = build_profile_text(profile)
                score = cold_start_score(text, self._keywords)
                pos_hits = [kw for kw in self._keywords["positive"] if kw in text]
                neg_hits = [kw for kw in self._keywords["negative"] if kw in text]
                lines = [
                    f"Cold-start heuristic score: {score:.1f}",
                    "  (normalized to batch min/max at ranking time for Thompson Sampling)",
                ]
                if pos_hits:
                    lines.append(f"  positive hits: {', '.join(pos_hits)}")
                if neg_hits:
                    lines.append(f"  negative hits: {', '.join(neg_hits)}")
                if not pos_hits and not neg_hits:
                    lines.append("  no keyword matches")
                return "\n".join(lines)
            return "Model not trained — no explanation available"

        import numpy as np

        features = self._extract_features(profile)
        X = np.array([features])
        base_prob = self._model.predict_proba(X)[0, 1]

        # Perturbation-based per-profile contributions:
        # replace each feature with its training mean, measure prediction shift
        contributions = []
        for i, (name, val) in enumerate(zip(self._all_feature_names, features)):
            X_perturbed = X.copy()
            X_perturbed[0, i] = self._feature_means[i]
            perturbed_prob = self._model.predict_proba(X_perturbed)[0, 1]
            contrib = base_prob - perturbed_prob
            contributions.append((name, contrib, val))

        contributions.sort(key=lambda x: abs(x[1]), reverse=True)

        lines = [
            f"Predicted acceptance probability: {base_prob:.3f}",
            "Feature contributions (prediction shift when replaced with training mean):",
        ]
        for name, contrib, val in contributions[:15]:
            sign = "+" if contrib >= 0 else ""
            lines.append(f"  {name:45s} {sign}{contrib:8.4f}  (val={val})")

        if len(contributions) > 15:
            remaining = sum(abs(c[1]) for c in contributions[15:])
            lines.append(f"  ... {len(contributions) - 15} more features (total |contrib|={remaining:.4f})")

        return "\n".join(lines)
