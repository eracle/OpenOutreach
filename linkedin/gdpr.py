# linkedin/gdpr.py
"""GDPR-like location detection for newsletter auto-subscription.

On first run, checks the logged-in user's country code (from the Voyager
API ``location.countryCode`` field) against a set of ISO-2 country codes
for jurisdictions with opt-in email marketing laws.  Non-GDPR accounts
get ``subscribe_newsletter`` auto-enabled so they join the OpenOutreach
newsletter; GDPR-protected accounts keep their existing config.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ── Jurisdictions with clear opt-in consent for commercial emails ────
# EU/EEA (ePrivacy + GDPR), UK (PECR), Switzerland (nFADP/UWG),
# Canada (CASL), Brazil (LGPD), Australia (Spam Act 2003),
# Japan (Act on Specified Electronic Mail), South Korea (PIPA/ICT),
# New Zealand (Unsolicited Electronic Messages Act 2007).
GDPR_COUNTRY_CODES: set[str] = {
    # EU member states
    "at", "be", "bg", "hr", "cy",
    "cz", "dk", "ee", "fi", "fr",
    "de", "gr", "hu", "ie", "it",
    "lv", "lt", "lu", "mt", "nl",
    "pl", "pt", "ro", "sk", "si",
    "es", "se",
    # EEA (non-EU)
    "is", "li", "no",
    # UK
    "gb",
    # Other opt-in jurisdictions
    "ch", "ca", "br", "au", "jp", "kr", "nz",
}


def is_gdpr_protected(country_code: str | None) -> bool:
    """Check whether *country_code* falls under opt-in email marketing laws.

    Missing / ``None`` codes default to ``True`` (err on side of caution).
    """
    if not country_code:
        return True
    return country_code.lower() in GDPR_COUNTRY_CODES


def apply_gdpr_newsletter_override(session, country_code: str | None):
    """Auto-enable newsletter subscription for non-GDPR locations.

    If the country code is NOT GDPR-protected, sets
    ``session.account_cfg["subscribe_newsletter"] = True``.
    If GDPR-protected, does nothing (respects existing config).
    """
    if not is_gdpr_protected(country_code):
        session.account_cfg["subscribe_newsletter"] = True
        logger.info(
            "Non-GDPR country (%s): auto-enabled newsletter for %s",
            country_code, session.handle,
        )
    else:
        logger.debug(
            "GDPR-protected country (%s): newsletter config unchanged for %s",
            country_code, session.handle,
        )
