# openoutreach/linkedin/setup/geo.py
"""Country-code jurisdiction detection — two separate regime lines.

Both read the logged-in user's (or a lead's) ISO-2 country code from the
Voyager API ``location.countryCode`` field, but answer different questions:

- ``is_gdpr_protected`` / ``GDPR_COUNTRY_CODES`` — the broad *email-marketing
  opt-in* set (EU/EEA + UK + CH + CA/BR/AU/JP/KR/NZ).  Drives newsletter
  auto-subscription: non-protected accounts get ``subscribe_newsletter``
  auto-enabled; protected accounts keep their existing config.
- ``is_eea_located`` / ``EEA_UK_CH`` — the narrower *data-collection regime*
  set (EU/EEA + UK + CH only).  Gates contribution into the central contacts
  store and the user-level forced-give-back override.
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


# ── Data-collection regime line (EEA/UK/CH) ──────────────────────────
# Narrower than GDPR_COUNTRY_CODES above: the set that governs whether we
# may *collect* a profile into the central contacts store, NOT the broader
# email-marketing-consent set.  EU-27 + EEA (NO/IS/LI) + UK + Switzerland
# only — deliberately excludes ca/br/au/jp/kr/nz (their email-opt-in laws
# don't bear on collection), so Brazil/Canada/etc. leads are collectable.
EEA_UK_CH: set[str] = {
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
    # Switzerland
    "ch",
}


def is_eea_located(country_code: str | None) -> bool:
    """Check whether *country_code* is in the EEA/UK/CH data-collection regime.

    Gates contribution to the central contacts store (a located profile is
    dropped, never stored) and the user-level forced-give-back override.
    Missing / ``None`` / blank codes default to ``True`` (err on the side of
    exclusion — a false drop costs one lead, a false keep is the only risk).
    """
    if not country_code or not country_code.strip():
        return True
    return country_code.strip().lower() in EEA_UK_CH


def apply_gdpr_newsletter_override(session, country_code: str | None):
    """Auto-enable newsletter subscription for non-GDPR locations.

    If the country code is NOT GDPR-protected, sets
    ``session.linkedin_profile.subscribe_newsletter = True`` and saves.
    If GDPR-protected, does nothing (respects existing config).
    """
    if not is_gdpr_protected(country_code):
        session.linkedin_profile.subscribe_newsletter = True
        session.linkedin_profile.save(update_fields=["subscribe_newsletter"])
        logger.info(
            "Non-GDPR country (%s): auto-enabled newsletter for %s",
            country_code, session,
        )
    else:
        logger.debug(
            "GDPR-protected country (%s): newsletter config unchanged for %s",
            country_code, session,
        )
