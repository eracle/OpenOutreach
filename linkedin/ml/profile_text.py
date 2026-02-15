# linkedin/ml/profile_text.py
from __future__ import annotations


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
