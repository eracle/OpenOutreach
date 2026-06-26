"""Tests for the LLM model factory and onboarding credential verification."""
import pytest

from openoutreach.core import llm


def test_build_llm_model_unknown_provider_raises():
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        llm.build_llm_model("nope:some-model", "key")


def test_verify_llm_credentials_ok(monkeypatch):
    monkeypatch.setattr(llm, "_ping_model", lambda *a, **k: None)
    assert llm.verify_llm_credentials("anthropic:claude", "sk-key") is None


def test_verify_llm_credentials_reports_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("invalid api key")

    monkeypatch.setattr(llm, "_ping_model", boom)
    assert llm.verify_llm_credentials("anthropic:claude", "bad") == "invalid api key"
