# linkedin/ml/keywords.py
from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


def load_keywords(path: Path) -> dict:
    """Load campaign keywords YAML.

    Returns {"positive": [...], "negative": [...], "exploratory": [...]}.
    All keywords are lowercased. Returns empty lists if file is missing.
    """
    empty = {"positive": [], "negative": [], "exploratory": []}
    if not path.exists():
        return empty

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    return {
        "positive": [kw.lower().strip() for kw in (data.get("positive") or [])],
        "negative": [kw.lower().strip() for kw in (data.get("negative") or [])],
        "exploratory": [kw.lower().strip() for kw in (data.get("exploratory") or [])],
    }


def build_profile_text(profile: dict) -> str:
    """Concatenate all text fields from in-memory profile dict, lowercased.

    Mirrors the SQL profile_text concatenation order:
    headline + summary + location_name + industry.name +
    position titles/companies/locations/descriptions +
    education schools/degrees/fields
    """
    p = profile.get("profile", {}) or {}
    parts = [
        p.get("headline", "") or "",
        p.get("summary", "") or "",
        p.get("location_name", "") or "",
    ]

    industry = p.get("industry", {}) or {}
    parts.append(industry.get("name", "") or "")

    for pos in p.get("positions", []) or []:
        parts.append(pos.get("title", "") or "")
        parts.append(pos.get("company_name", "") or "")
        parts.append(pos.get("location", "") or "")
        parts.append(pos.get("description", "") or "")

    for edu in p.get("educations", []) or []:
        parts.append(edu.get("school_name", "") or "")
        parts.append(edu.get("degree", "") or "")
        parts.append(edu.get("field_of_study", "") or "")

    return " ".join(parts).lower()


def keyword_feature_names(keywords: dict) -> list[str]:
    """Return ordered list of human-readable keyword feature names."""
    labels = {"positive": "positive keyword", "negative": "negative keyword", "exploratory": "exploratory keyword"}
    names = []
    for category in ("positive", "negative", "exploratory"):
        for kw in keywords.get(category, []):
            names.append(f"{labels[category]}: {kw.strip()}")
    return names


def compute_keyword_features(text: str, keywords: dict) -> list[float]:
    """Boolean presence (1/0) of each keyword in text. Same order as keyword_feature_names()."""
    text_lower = text.lower()
    flags = []
    for category in ("positive", "negative", "exploratory"):
        for kw in keywords.get(category, []):
            flags.append(1.0 if kw in text_lower else 0.0)
    return flags


def cold_start_score(text: str, keywords: dict) -> float:
    """Heuristic score: number of distinct positive keywords present minus negative.

    Each keyword contributes at most +1 or -1.
    Exploratory keywords are ignored (they're for exploration/exploitation balance).
    """
    text_lower = text.lower()
    pos = sum(1 for kw in keywords.get("positive", []) if kw in text_lower)
    neg = sum(1 for kw in keywords.get("negative", []) if kw in text_lower)
    return float(pos - neg)
