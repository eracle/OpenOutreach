# tests/test_gdpr.py
import pytest

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


class _FakeSession:
    def __init__(self, handle="testuser"):
        self.handle = handle
        self.account_cfg = {"subscribe_newsletter": None}


def test_override_non_gdpr_sets_true():
    session = _FakeSession()
    apply_gdpr_newsletter_override(session, "us")
    assert session.account_cfg["subscribe_newsletter"] is True


def test_override_gdpr_respects_existing_config():
    session = _FakeSession()
    session.account_cfg["subscribe_newsletter"] = False
    apply_gdpr_newsletter_override(session, "de")
    assert session.account_cfg["subscribe_newsletter"] is False


def test_override_missing_code_respects_existing_config():
    session = _FakeSession()
    session.account_cfg["subscribe_newsletter"] = False
    apply_gdpr_newsletter_override(session, None)
    assert session.account_cfg["subscribe_newsletter"] is False
