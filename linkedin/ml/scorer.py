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

# 28 mechanical features matching the mart column names exactly
MECHANICAL_FEATURES = [
    "connection_degree",
    "num_positions",
    "num_educations",
    "has_summary",
    "headline_length",
    "summary_length",
    "has_industry",
    "has_geo",
    "has_location",
    "has_company",
    "num_distinct_companies",
    "positions_with_description_ratio",
    "num_position_locations",
    "total_description_length",
    "has_position_descriptions",
    "is_currently_employed",
    "avg_title_length",
    "years_experience",
    "current_position_tenure_months",
    "avg_position_tenure_months",
    "longest_tenure_months",
    "has_education_degree",
    "has_field_of_study",
    "has_multiple_degrees",
]


class ProfileScorer:
    def __init__(self, seed: int = 42, keywords_path=None):
        import numpy as np

        self._rng = np.random.RandomState(seed)
        self._model = None
        self._trained = False
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
        from sklearn.linear_model import SGDClassifier

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
            logger.warning("Only %d rows in training data — need at least 10", len(df))
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

        model = SGDClassifier(
            loss="log_loss",
            penalty="elasticnet",
            l1_ratio=0.5,
            random_state=42,
            max_iter=1000,
        )
        model.fit(X, y)

        self._model = model
        self._trained = True
        n_features = X.shape[1]
        logger.info(
            "ML model trained on %d rows, %d features (%.1f%% accepted)",
            len(df), n_features, y.mean() * 100,
        )
        return True

    def _extract_mechanical_features(self, profile: dict) -> list:
        """Compute 28 mechanical features from in-memory profile dict."""
        p = profile.get("profile", {}) or {}
        positions = p.get("positions", []) or []
        educations = p.get("educations", []) or []
        summary = p.get("summary", "") or ""
        headline = p.get("headline", "") or ""
        industry = p.get("industry", {}) or {}
        geo = p.get("geo", {}) or {}
        location_name = p.get("location_name", "") or ""
        company_name = p.get("company_name", "") or ""

        # Position-derived features
        num_distinct_companies = len({
            pos.get("company_name", "")
            for pos in positions
            if pos.get("company_name")
        })
        pos_with_desc = sum(1 for pos in positions if pos.get("description"))
        positions_with_description_ratio = pos_with_desc / len(positions) if positions else 0.0
        num_position_locations = len({
            pos.get("location", "")
            for pos in positions
            if pos.get("location")
        })
        total_description_length = sum(
            len(pos.get("description", "") or "") for pos in positions
        )
        has_position_descriptions = 1 if pos_with_desc > 0 else 0

        now_frac = date.today().year + date.today().month / 12.0
        is_currently_employed = 0
        title_lengths = []
        tenures = []
        start_fracs = []
        end_fracs = []
        current_tenure = 0.0

        for pos in positions:
            title_lengths.append(len(pos.get("title", "") or ""))
            start_year = pos.get("start_year")
            start_month = pos.get("start_month") or 1
            end_year = pos.get("end_year")
            end_month = pos.get("end_month") or 1
            is_current = end_year is None

            if is_current:
                is_currently_employed = 1

            if start_year is not None:
                sf = start_year + start_month / 12.0
                ef = (end_year + end_month / 12.0) if end_year else now_frac
                start_fracs.append(sf)
                end_fracs.append(ef)
                tenure_months = (ef - sf) * 12
                tenures.append(tenure_months)
                if is_current:
                    current_tenure = max(current_tenure, tenure_months)

        avg_title_length = sum(title_lengths) / len(title_lengths) if title_lengths else 0.0
        years_experience = (max(end_fracs) - min(start_fracs)) if start_fracs else 0.0
        avg_position_tenure_months = sum(tenures) / len(tenures) if tenures else 0.0
        longest_tenure_months = max(tenures) if tenures else 0.0

        # Education-derived features
        has_education_degree = 0
        has_field_of_study = 0
        degree_count = 0
        for edu in educations:
            if edu.get("degree"):
                has_education_degree = 1
                degree_count += 1
            if edu.get("field_of_study"):
                has_field_of_study = 1
        has_multiple_degrees = 1 if degree_count > 1 else 0

        return [
            p.get("connection_degree") or 0,
            len(positions),
            len(educations),
            1 if summary else 0,
            len(headline),
            len(summary),
            1 if industry.get("name") else 0,
            1 if geo.get("defaultLocalizedNameWithoutCountryName") else 0,
            1 if location_name else 0,
            1 if company_name else 0,
            num_distinct_companies,
            positions_with_description_ratio,
            num_position_locations,
            total_description_length,
            has_position_descriptions,
            is_currently_employed,
            avg_title_length,
            years_experience,
            current_tenure,
            avg_position_tenure_months,
            longest_tenure_months,
            has_education_degree,
            has_field_of_study,
            has_multiple_degrees,
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

        # Untrained: cold-start heuristic or FIFO
        if not self._trained:
            if self.has_keywords:
                scored = []
                for p in profiles:
                    text = build_profile_text(p)
                    score = cold_start_score(text, self._keywords)
                    scored.append((score, p))
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
                lines = [f"Cold-start heuristic score: {score:.1f}"]
                if pos_hits:
                    lines.append(f"  positive hits: {', '.join(pos_hits)}")
                if neg_hits:
                    lines.append(f"  negative hits: {', '.join(neg_hits)}")
                if not pos_hits and not neg_hits:
                    lines.append("  no keyword matches")
                return "\n".join(lines)
            return "Model not trained — no explanation available"

        features = self._extract_features(profile)
        coefs = self._model.coef_[0]

        contributions = []
        for name, coef, val in zip(self._all_feature_names, coefs, features):
            contributions.append((name, coef * val, coef, val))

        contributions.sort(key=lambda x: abs(x[1]), reverse=True)

        lines = ["Feature contributions (coef * value):"]
        for name, contrib, coef, val in contributions[:15]:
            sign = "+" if contrib >= 0 else ""
            lines.append(f"  {name:45s} {sign}{contrib:8.4f}  (coef={coef:.4f}, val={val})")

        if len(contributions) > 15:
            remaining = sum(abs(c[1]) for c in contributions[15:])
            lines.append(f"  ... {len(contributions) - 15} more features (total |contrib|={remaining:.4f})")

        return "\n".join(lines)
