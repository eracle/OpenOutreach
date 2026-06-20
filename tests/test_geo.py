# tests/test_geo.py
import pytest
from unittest.mock import MagicMock

from openoutreach.linkedin.setup.geo import (
    EEA_UK_CH,
    GDPR_COUNTRY_CODES,
    apply_gdpr_contribution_override,
    apply_gdpr_newsletter_override,
    is_eea_located,
    is_gdpr_protected,
)


# ── Country code lookup ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "code,expected",
    [
        # EU
        ("de", True),
        ("fr", True),
        ("it", True),
        ("es", True),
        ("pl", True),
        ("nl", True),
        ("se", True),
        ("ie", True),
        ("dk", True),
        # EEA
        ("no", True),
        ("is", True),
        # UK
        ("gb", True),
        # Other opt-in
        ("ch", True),
        ("ca", True),
        ("br", True),
        ("au", True),
        ("jp", True),
        ("kr", True),
        ("nz", True),
        # Non-GDPR
        ("us", False),
        ("ae", False),
        ("sg", False),
        ("in", False),
        ("ng", False),
        ("sa", False),
    ],
)
def test_country_code_lookup(code, expected):
    assert is_gdpr_protected(code) is expected


def test_case_insensitivity():
    assert is_gdpr_protected("DE") is True
    assert is_gdpr_protected("Us") is False


def test_missing_country_code_defaults_to_protected():
    assert is_gdpr_protected(None) is True
    assert is_gdpr_protected("") is True


def test_all_eu_members_present():
    eu_codes = {
        "at", "be", "bg", "hr", "cy", "cz", "dk", "ee", "fi", "fr",
        "de", "gr", "hu", "ie", "it", "lv", "lt", "lu", "mt", "nl",
        "pl", "pt", "ro", "sk", "si", "es", "se",
    }
    assert eu_codes.issubset(GDPR_COUNTRY_CODES)


# ── apply_gdpr_newsletter_override ───────────────────────────────────


def _make_fake_session(subscribe=None):
    """Create a fake session with a linkedin_profile mock."""
    session = MagicMock()
    session.linkedin_profile.subscribe_newsletter = subscribe
    return session


def test_override_non_gdpr_sets_true():
    session = _make_fake_session(subscribe=None)
    apply_gdpr_newsletter_override(session, "us")
    assert session.linkedin_profile.subscribe_newsletter is True
    session.linkedin_profile.save.assert_called_once()


def test_override_gdpr_respects_existing_config():
    session = _make_fake_session(subscribe=False)
    apply_gdpr_newsletter_override(session, "de")
    assert session.linkedin_profile.subscribe_newsletter is False


def test_override_missing_code_respects_existing_config():
    session = _make_fake_session(subscribe=False)
    apply_gdpr_newsletter_override(session, None)
    assert session.linkedin_profile.subscribe_newsletter is False


# ── is_eea_located (data-collection regime) ──────────────────────────


@pytest.mark.parametrize(
    "code,expected",
    [
        # EU
        ("de", True),
        ("fr", True),
        ("es", True),
        ("ie", True),
        # EEA non-EU
        ("no", True),
        ("is", True),
        ("li", True),
        # UK + CH
        ("gb", True),
        ("ch", True),
        # NOT in the collection-regime set (collectable) — these are the
        # email-opt-in countries that GDPR_COUNTRY_CODES wrongly catches.
        ("br", False),
        ("ca", False),
        ("au", False),
        ("jp", False),
        ("kr", False),
        ("nz", False),
        # Plainly non-protected
        ("us", False),
        ("in", False),
        ("ae", False),
        ("sg", False),
        ("ng", False),
    ],
)
def test_eea_located_lookup(code, expected):
    assert is_eea_located(code) is expected


def test_eea_located_case_insensitivity():
    assert is_eea_located("DE") is True
    assert is_eea_located("Br") is False


def test_eea_located_missing_or_blank_defaults_to_excluded():
    assert is_eea_located(None) is True
    assert is_eea_located("") is True
    assert is_eea_located("   ") is True


def test_eea_uk_ch_excludes_email_optin_countries():
    # Brazil (LATAM market) and friends must NOT be in the collection set.
    assert {"br", "ca", "au", "jp", "kr", "nz"}.isdisjoint(EEA_UK_CH)
    # but the full EEA/UK/CH line is present
    assert {"de", "fr", "no", "is", "li", "gb", "ch"}.issubset(EEA_UK_CH)


# ── apply_gdpr_contribution_override (nationality-driven, not asked) ──


def _contribution_session(contribute=None):
    session = MagicMock()
    session.linkedin_profile.contribute_to_hub = contribute
    return session


def test_contribution_override_enables_outside_eea():
    session = _contribution_session(contribute=False)
    apply_gdpr_contribution_override(session, "us")
    assert session.linkedin_profile.contribute_to_hub is True
    session.linkedin_profile.save.assert_called_once()


def test_contribution_override_disables_inside_eea():
    session = _contribution_session(contribute=True)
    apply_gdpr_contribution_override(session, "de")
    assert session.linkedin_profile.contribute_to_hub is False
    session.linkedin_profile.save.assert_called_once()


def test_contribution_override_missing_code_disables():
    # Unknown location errs on the side of exclusion (is_eea_located → True).
    session = _contribution_session(contribute=True)
    apply_gdpr_contribution_override(session, None)
    assert session.linkedin_profile.contribute_to_hub is False


def test_contribution_override_no_write_when_unchanged():
    session = _contribution_session(contribute=True)
    apply_gdpr_contribution_override(session, "us")
    assert session.linkedin_profile.contribute_to_hub is True
    session.linkedin_profile.save.assert_not_called()
