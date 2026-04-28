"""LLM model factory: build a pydantic-ai `Model` from `SiteConfig`.

Single boundary for LLM construction. Call sites import `get_llm_model()` and
hand the result to `pydantic_ai.Agent(...)`. Provider-specific routing lives
here so the rest of the codebase stays provider-agnostic.

Importing this module also applies ``nest_asyncio`` once. pydantic-ai's
``Agent.run_sync`` wraps an async ``run`` in ``loop.run_until_complete``;
something in its internals (anyio task group / portal) leaves the daemon
thread's running-loop slot populated across calls, which trips the
re-entrancy guard in ``BaseEventLoop._check_running`` on every subsequent
``run_sync`` (``RuntimeError: This/Cannot run the event loop``). The
official pydantic-ai troubleshooting recipe — same one used for Jupyter /
Colab / Marimo — is ``nest_asyncio.apply()``, which patches the loop to
allow nested ``run_until_complete``. See:
https://pydantic.dev/docs/ai/overview/troubleshooting/
"""
from __future__ import annotations

import nest_asyncio

nest_asyncio.apply()


# Override the SDK default of 2. Each retry uses the SDK's built-in jittered
# exponential backoff and honors `Retry-After`, so 8 attempts ride through
# typical 429/529 capacity blips (~1–2 minutes) instead of failing in ~1.5s.
_MAX_RETRIES = 8


# ── Per-provider builders ──

def _build_openai(cfg):
    from openai import AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIModel
    from pydantic_ai.providers.openai import OpenAIProvider
    client = AsyncOpenAI(api_key=cfg.llm_api_key, max_retries=_MAX_RETRIES)
    return OpenAIModel(cfg.ai_model, provider=OpenAIProvider(openai_client=client))


def _build_anthropic(cfg):
    from anthropic import AsyncAnthropic
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.providers.anthropic import AnthropicProvider
    client = AsyncAnthropic(api_key=cfg.llm_api_key, max_retries=_MAX_RETRIES)
    return AnthropicModel(cfg.ai_model, provider=AnthropicProvider(anthropic_client=client))


def _build_google(cfg):
    from pydantic_ai.models.google import GoogleModel
    from pydantic_ai.providers.google import GoogleProvider
    return GoogleModel(cfg.ai_model, provider=GoogleProvider(api_key=cfg.llm_api_key))


def _build_groq(cfg):
    from groq import AsyncGroq
    from pydantic_ai.models.groq import GroqModel
    from pydantic_ai.providers.groq import GroqProvider
    client = AsyncGroq(api_key=cfg.llm_api_key, max_retries=_MAX_RETRIES)
    return GroqModel(cfg.ai_model, provider=GroqProvider(groq_client=client))


def _build_mistral(cfg):
    from pydantic_ai.models.mistral import MistralModel
    from pydantic_ai.providers.mistral import MistralProvider
    return MistralModel(cfg.ai_model, provider=MistralProvider(api_key=cfg.llm_api_key))


def _build_cohere(cfg):
    from pydantic_ai.models.cohere import CohereModel
    from pydantic_ai.providers.cohere import CohereProvider
    return CohereModel(cfg.ai_model, provider=CohereProvider(api_key=cfg.llm_api_key))


def _build_openai_compatible(cfg):
    if not cfg.llm_api_base:
        raise ValueError("LLM_API_BASE is required for the openai_compatible provider.")
    from pydantic_ai.models.openai import OpenAIModel
    from pydantic_ai.providers.openai import OpenAIProvider
    return OpenAIModel(cfg.ai_model, provider=OpenAIProvider(
        base_url=cfg.llm_api_base, api_key=cfg.llm_api_key,
    ))


_PROVIDER_BUILDERS = {
    "openai": _build_openai,
    "anthropic": _build_anthropic,
    "google": _build_google,
    "groq": _build_groq,
    "mistral": _build_mistral,
    "cohere": _build_cohere,
    "openai_compatible": _build_openai_compatible,
}


# ── Public API ──

def _validated_site_config():
    """Load `SiteConfig` and assert the required LLM fields are populated."""
    from linkedin.models import SiteConfig

    cfg = SiteConfig.load()
    if not cfg.llm_api_key:
        raise ValueError("LLM_API_KEY is not set in Site Configuration.")
    if not cfg.ai_model:
        raise ValueError("AI_MODEL is not set in Site Configuration.")
    return cfg


def get_llm_model():
    """Return a configured pydantic-ai `Model` for the current `SiteConfig`."""
    cfg = _validated_site_config()
    builder = _PROVIDER_BUILDERS.get(cfg.llm_provider)
    if builder is None:
        raise ValueError(f"Unknown LLM provider: {cfg.llm_provider!r}")
    return builder(cfg)
