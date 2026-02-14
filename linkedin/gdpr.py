# linkedin/gdpr.py
"""GDPR-like location detection for newsletter auto-subscription.

On first run, checks the logged-in user's LinkedIn location against a
keyword list of jurisdictions with opt-in email marketing laws.  Falls
back to an LLM call for unrecognised locations.  Non-GDPR accounts get
``subscribe_newsletter`` auto-enabled so they join the OpenOutreach
newsletter; GDPR-protected accounts keep their existing config.
"""
from __future__ import annotations

import logging
import re

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ── Jurisdictions with clear opt-in consent for commercial emails ────
# EU/EEA (ePrivacy + GDPR), UK (PECR), Switzerland (nFADP/UWG),
# Canada (CASL), Brazil (LGPD), Australia (Spam Act 2003),
# Japan (Act on Specified Electronic Mail), South Korea (PIPA/ICT),
# New Zealand (Unsolicited Electronic Messages Act 2007).
GDPR_LOCATION_KEYWORDS: list[str] = [
    # EU member states
    "austria", "belgium", "bulgaria", "croatia", "cyprus",
    "czech republic", "czechia", "denmark", "estonia", "finland",
    "france", "germany", "greece", "hungary", "ireland",
    "italy", "latvia", "lithuania", "luxembourg", "malta",
    "netherlands", "poland", "portugal", "romania", "slovakia",
    "slovenia", "spain", "sweden",
    # EEA (non-EU)
    "iceland", "liechtenstein", "norway",
    # UK
    "united kingdom", "england", "scotland", "wales", "northern ireland",
    # Other opt-in jurisdictions
    "switzerland", "canada", "brazil", "brasil",
    "australia", "japan", "south korea", "new zealand",
    # Major EU/EEA cities (LinkedIn sometimes omits country)
    "berlin", "munich", "frankfurt", "hamburg",
    "paris", "lyon", "marseille",
    "madrid", "barcelona",
    "milan", "rome",
    "amsterdam", "rotterdam",
    "brussels", "antwerp",
    "vienna",
    "prague",
    "warsaw",
    "copenhagen",
    "stockholm",
    "helsinki",
    "dublin",
    "lisbon",
    "athens",
    "budapest",
    "bucharest",
    # Major non-EU opt-in cities
    "london", "manchester", "edinburgh",
    "zurich", "geneva",
    "toronto", "montreal", "vancouver",
    "sydney", "melbourne",
    "tokyo", "osaka",
    "seoul",
    "auckland", "wellington",
]

# Pre-compiled word-boundary patterns (avoids "india" matching "Indiana", etc.)
_KW_PATTERNS: list[re.Pattern] = [
    re.compile(r"\b" + re.escape(kw) + r"\b") for kw in GDPR_LOCATION_KEYWORDS
]

_GDPR_CHECK_PROMPT = (
    'Does the LinkedIn location "{location}" fall under a jurisdiction that '
    "requires explicit opt-in consent for commercial email newsletters "
    "(e.g. EU/EEA GDPR, UK PECR, Canada CASL, Brazil LGPD, Australia Spam Act, "
    "Japan Specified Electronic Mail Act, South Korea PIPA, "
    "New Zealand Unsolicited Electronic Messages Act, Switzerland nFADP)?"
)


class GdprCheckResult(BaseModel):
    """LLM structured output for GDPR location check."""
    is_protected: bool = Field(description="True if the location requires opt-in consent for email newsletters")


def check_gdpr_by_keywords(location: str) -> bool | None:
    """Return True if *location* matches a known GDPR-like jurisdiction.

    Returns ``None`` when no keyword matches (caller should fall back to LLM).
    """
    loc_lower = location.lower()
    for pat in _KW_PATTERNS:
        if pat.search(loc_lower):
            return True
    return None


def check_gdpr_by_llm(location: str) -> bool:
    """Ask an LLM whether *location* is in a GDPR-like jurisdiction."""
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_openai import ChatOpenAI

    from linkedin.conf import AI_MODEL, LLM_API_KEY, LLM_API_BASE

    llm = ChatOpenAI(model=AI_MODEL, api_key=LLM_API_KEY, base_url=LLM_API_BASE)
    chain = (
        ChatPromptTemplate.from_messages([("human", "{prompt}")])
        | llm.with_structured_output(GdprCheckResult)
    )
    result = chain.invoke({"prompt": _GDPR_CHECK_PROMPT.format(location=location)})
    return result.is_protected


def is_gdpr_protected(location: str | None) -> bool:
    """Check whether *location* falls under opt-in email marketing laws.

    Empty / ``None`` locations default to ``True`` (err on side of caution).
    Keywords are tried first; LLM is called only when no keyword matches.
    """
    if not location:
        return True

    result = check_gdpr_by_keywords(location)
    if result is not None:
        return result

    return check_gdpr_by_llm(location)


def apply_gdpr_newsletter_override(session, location: str | None):
    """Auto-enable newsletter subscription for non-GDPR locations.

    If the location is NOT GDPR-protected, sets
    ``session.account_cfg["subscribe_newsletter"] = True``.
    If GDPR-protected, does nothing (respects existing config).
    """
    if not is_gdpr_protected(location):
        session.account_cfg["subscribe_newsletter"] = True
        logger.info(
            "Non-GDPR location (%s): auto-enabled newsletter for %s",
            location, session.handle,
        )
    else:
        logger.debug(
            "GDPR-protected location (%s): newsletter config unchanged for %s",
            location, session.handle,
        )
