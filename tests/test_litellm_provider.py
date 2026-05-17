"""Tests for the LiteLLM provider integration."""
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def site_config(db):
    from linkedin.models import SiteConfig

    cfg = SiteConfig.load()
    cfg.llm_provider = "litellm"
    cfg.ai_model = "anthropic/claude-sonnet-4-20250514"
    cfg.llm_api_base = "http://localhost:4000/v1"
    cfg.llm_api_key = ""
    cfg.save()
    return cfg


class TestLiteLLMProviderChoice:
    def test_litellm_in_choices(self):
        from linkedin.models import SiteConfig

        values = [c[0] for c in SiteConfig.LLMProvider.choices]
        assert "litellm" in values

    def test_litellm_label(self):
        from linkedin.models import SiteConfig

        labels = dict(SiteConfig.LLMProvider.choices)
        assert labels["litellm"] == "LiteLLM"


class TestBuildLiteLLM:
    def test_builds_openai_model(self, site_config):
        from linkedin.llm import _build_litellm

        model = _build_litellm(site_config)
        assert model is not None
        assert model.model_name == "anthropic/claude-sonnet-4-20250514"

    def test_requires_api_base(self, site_config):
        from linkedin.llm import _build_litellm

        site_config.llm_api_base = ""
        with pytest.raises(ValueError, match="LLM_API_BASE is required"):
            _build_litellm(site_config)

    def test_api_key_optional(self, site_config):
        from linkedin.llm import _build_litellm

        site_config.llm_api_key = ""
        model = _build_litellm(site_config)
        assert model is not None

    def test_api_key_forwarded_when_set(self, site_config):
        from linkedin.llm import _build_litellm

        site_config.llm_api_key = "sk-litellm-test-key"
        model = _build_litellm(site_config)
        assert model is not None


class TestValidatedSiteConfig:
    def test_litellm_skips_api_key_check(self, site_config):
        from linkedin.llm import _validated_site_config

        site_config.llm_api_key = ""
        site_config.save()
        cfg = _validated_site_config()
        assert cfg.llm_provider == "litellm"

    def test_litellm_still_requires_model(self, site_config):
        from linkedin.llm import _validated_site_config

        site_config.ai_model = ""
        site_config.save()
        with pytest.raises(ValueError, match="AI_MODEL is not set"):
            _validated_site_config()

    def test_openai_still_requires_api_key(self, db):
        from linkedin.models import SiteConfig
        from linkedin.llm import _validated_site_config

        cfg = SiteConfig.load()
        cfg.llm_provider = "openai"
        cfg.ai_model = "gpt-4o"
        cfg.llm_api_key = ""
        cfg.save()
        with pytest.raises(ValueError, match="LLM_API_KEY is not set"):
            _validated_site_config()


class TestGetLLMModel:
    def test_litellm_end_to_end(self, site_config):
        from linkedin.llm import get_llm_model

        model = get_llm_model()
        assert model is not None

    def test_litellm_with_hosted_key(self, site_config):
        from linkedin.llm import get_llm_model

        site_config.llm_api_key = "sk-hosted-proxy-key"
        site_config.save()
        model = get_llm_model()
        assert model is not None
