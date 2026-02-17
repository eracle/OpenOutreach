# tests/test_gdpr.py
import pytest
from unittest.mock import MagicMock

from linkedin.gdpr import (
    GDPR_COUNTRY_CODES,
    apply_gdpr_newsletter_override,
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
    session.handle = "testuser"
    session.linkedin_profile.subscribe_newsletter = subscribe
    session.account_cfg = {"subscribe_newsletter": subscribe}
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
