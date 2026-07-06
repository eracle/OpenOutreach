# tests/test_onboarding_wizard.py
"""The onboarding prompt primitives.

Each prompt owns its validation loop and returns a value or ``None`` (cancel).
The point these guard: an optional field returns ``""`` instead of re-asking
(so onboarding never gets stuck on a blank optional answer), a required field
re-asks a blank, and a bad value re-asks the *same* field — it never rewinds.
"""
from unittest.mock import Mock, patch

from openoutreach.core import onboarding_wizard as wiz


def _questionary(returns):
    """Patch a questionary prompt so successive ``.ask()`` calls yield *returns*."""
    prompt = Mock()
    prompt.ask.side_effect = list(returns)
    return Mock(return_value=prompt)


def test_text_returns_stripped_value():
    with patch("questionary.text", _questionary(["  hi  "])):
        assert wiz.text("m") == "hi"


def test_text_reasks_blank_required_then_accepts():
    with patch("questionary.text", _questionary(["", "  ", "ok"])) as q:
        assert wiz.text("m") == "ok"
    assert q.return_value.ask.call_count == 3


def test_text_optional_blank_returns_empty_without_reasking():
    with patch("questionary.text", _questionary([""])) as q:
        assert wiz.text("m", required=False) == ""
    assert q.return_value.ask.call_count == 1


def test_text_cancel_returns_none():
    with patch("questionary.text", _questionary([None])):
        assert wiz.text("m") is None


def test_text_validate_reasks_until_valid():
    check = lambda v: True if "@" in v else "need an @"
    with patch("questionary.text", _questionary(["nope", "a@b"])):
        assert wiz.text("m", validate=check) == "a@b"


def test_text_secret_uses_password_prompt():
    with patch("questionary.password", _questionary(["s3cret"])) as pw, \
         patch("questionary.text", _questionary(["WRONG"])):
        assert wiz.text("m", secret=True) == "s3cret"
    pw.assert_called_once()


def test_integer_reasks_non_numeric():
    with patch("questionary.text", _questionary(["x", "12"])):
        assert wiz.integer("m", default=1) == 12


def test_integer_cancel_returns_none():
    with patch("questionary.text", _questionary([None])):
        assert wiz.integer("m", default=1) is None


def test_confirm_passes_through():
    with patch("questionary.confirm", _questionary([False])):
        assert wiz.confirm("m", default=True) is False
