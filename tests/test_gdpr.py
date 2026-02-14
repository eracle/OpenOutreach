# tests/test_gdpr.py
from unittest.mock import patch

import pytest

from linkedin.gdpr import (
    GdprCheckResult,
    apply_gdpr_newsletter_override,
    check_gdpr_by_keywords,
    check_gdpr_by_llm,
    is_gdpr_protected,
)


# ── Keyword matching ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "location,expected",
    [
        ("Berlin, Germany", True),
        ("Greater Milan Metropolitan Area", True),
        ("Paris, Île-de-France, France", True),
        ("London, England, United Kingdom", True),
        ("Toronto, Ontario, Canada", True),
        ("Sydney, New South Wales, Australia", True),
        ("Tokyo, Japan", True),
        ("Seoul, South Korea", True),
        ("Auckland, New Zealand", True),
        ("São Paulo, Brazil", True),
        ("Zurich, Switzerland", True),
        ("Stockholm, Sweden", True),
        ("Dublin, Ireland", True),
        ("Copenhagen, Denmark", True),
        ("Warsaw, Poland", True),
        # Non-GDPR locations → no keyword match
        ("Austin, Texas, United States", None),
        ("Dubai, United Arab Emirates", None),
        ("Singapore", None),
        ("Mumbai, Maharashtra, India", None),
        ("Lagos, Nigeria", None),
        ("Riyadh, Saudi Arabia", None),
    ],
)
def test_keyword_matching(location, expected):
    assert check_gdpr_by_keywords(location) is expected


def test_case_insensitivity():
    assert check_gdpr_by_keywords("BERLIN, GERMANY") is True
    assert check_gdpr_by_keywords("london, england") is True
    assert check_gdpr_by_keywords("Paris, France") is True


def test_word_boundary_indiana_not_india():
    """'Indiana' must NOT match the 'india' keyword (India is not in the list anyway)."""
    assert check_gdpr_by_keywords("Indianapolis, Indiana, United States") is None


def test_word_boundary_no_partial_city_match():
    """Partial substrings should not trigger false positives."""
    # "Romanov" should not match "rome"
    assert check_gdpr_by_keywords("Romanov, Russia") is None


# ── LLM fallback ────────────────────────────────────────────────────


@patch("langchain_openai.ChatOpenAI")
@patch("langchain_core.prompts.ChatPromptTemplate")
def test_llm_returns_protected(mock_prompt_cls, mock_chat_cls):
    mock_prompt_cls.from_messages.return_value.__or__.return_value.invoke.return_value = (
        GdprCheckResult(is_protected=True)
    )
    assert check_gdpr_by_llm("Tallinn, Estonia") is True


@patch("langchain_openai.ChatOpenAI")
@patch("langchain_core.prompts.ChatPromptTemplate")
def test_llm_returns_not_protected(mock_prompt_cls, mock_chat_cls):
    mock_prompt_cls.from_messages.return_value.__or__.return_value.invoke.return_value = (
        GdprCheckResult(is_protected=False)
    )
    assert check_gdpr_by_llm("Houston, Texas") is False


# ── is_gdpr_protected (integration) ─────────────────────────────────


def test_empty_location_defaults_to_protected():
    assert is_gdpr_protected(None) is True
    assert is_gdpr_protected("") is True


def test_keyword_match_short_circuits_llm():
    with patch("linkedin.gdpr.check_gdpr_by_llm") as mock_llm:
        assert is_gdpr_protected("Berlin, Germany") is True
        mock_llm.assert_not_called()


@patch("linkedin.gdpr.check_gdpr_by_llm", return_value=False)
def test_unknown_location_falls_through_to_llm(mock_llm):
    assert is_gdpr_protected("Dubai, UAE") is False
    mock_llm.assert_called_once()


# ── apply_gdpr_newsletter_override ───────────────────────────────────


class _FakeSession:
    def __init__(self, handle="testuser"):
        self.handle = handle
        self.account_cfg = {"subscribe_newsletter": None}


@patch("linkedin.gdpr.is_gdpr_protected", return_value=False)
def test_override_non_gdpr_sets_true(mock_gdpr):
    session = _FakeSession()
    apply_gdpr_newsletter_override(session, "Austin, Texas")
    assert session.account_cfg["subscribe_newsletter"] is True


@patch("linkedin.gdpr.is_gdpr_protected", return_value=True)
def test_override_gdpr_respects_existing_config(mock_gdpr):
    session = _FakeSession()
    session.account_cfg["subscribe_newsletter"] = False
    apply_gdpr_newsletter_override(session, "Berlin, Germany")
    assert session.account_cfg["subscribe_newsletter"] is False
