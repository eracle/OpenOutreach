"""LLM model factory + sync boundary for pydantic-ai.

Two public entry points:

- `get_llm_model()` — builds a `pydantic_ai.Model` from `SiteConfig`,
  routing to the right provider.
- `run_agent_sync(coro)` — drives a pydantic-ai coroutine to completion
  from sync code, on a dedicated worker thread with a long-lived event
  loop. Used everywhere instead of `Agent.run_sync`.

Why a persistent worker thread (not `Agent.run_sync`, not `asyncio.run`):

- `Agent.run_sync` uses an anyio portal that leaves the caller thread's
  running-loop slot populated. Subsequent sync Playwright calls on the
  daemon thread then raise
  `"using Playwright Sync API inside the asyncio loop"`.
- `asyncio.run` per call closes its loop on exit. The openai / anthropic
  SDKs wrap `httpx.AsyncClient` in a subclass whose `__del__` does
  `get_running_loop().create_task(self.aclose())`. If GC fires the
  wrapper from call N during call N+1's loop, the cleanup task tries to
  close a transport bound to call N's now-closed loop →
  `RuntimeError: Event loop is closed`.

A single long-lived loop on a dedicated thread eliminates both: all HTTP
clients live on the same loop forever, and the runner thread's asyncio
slot stays inside this module — the caller thread is never touched.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Awaitable, Callable, TypeVar

_T = TypeVar("_T")

# Override the SDK default of 2. Each retry uses the SDK's built-in jittered
# exponential backoff and honors `Retry-After`, so 8 attempts ride through
# typical 429/529 capacity blips (~1–2 minutes) instead of failing in ~1.5s.
_MAX_RETRIES = 8


# ── Async runner ─────────────────────────────────────────────────────

class _AgentRunner:
    """Owns one persistent asyncio loop on a dedicated daemon thread.

    Construct lazily via `_get_runner()` so importing this module is free.
    The thread is a daemon, so no explicit shutdown is needed — it ends
    with the process.
    """

    def __init__(self):
        self._loop = asyncio.new_event_loop()
        ready = threading.Event()
        threading.Thread(
            target=self._serve, args=(ready,), daemon=True, name="llm-runner",
        ).start()
        ready.wait()

    def _serve(self, ready: threading.Event) -> None:
        asyncio.set_event_loop(self._loop)
        ready.set()
        self._loop.run_forever()

    def run(self, coro: Awaitable[_T]) -> _T:
        """Submit *coro* to the runner loop; block until it completes."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()


_runner: _AgentRunner | None = None
_runner_lock = threading.Lock()


def _get_runner() -> _AgentRunner:
    """Return the process-wide runner, creating it on first call."""
    global _runner
    if _runner is None:
        with _runner_lock:
            if _runner is None:
                _runner = _AgentRunner()
    return _runner


def run_agent_sync(coro: Awaitable[_T]) -> _T:
    """Drive *coro* on the dedicated LLM runner thread + loop."""
    return _get_runner().run(coro)


# ── Per-provider builders ────────────────────────────────────────────

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


def _build_litellm(cfg):
    if not cfg.llm_api_base:
        raise ValueError("LLM_API_BASE is required for the litellm provider (your LiteLLM proxy URL).")
    from openai import AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIModel
    from pydantic_ai.providers.openai import OpenAIProvider
    client = AsyncOpenAI(
        api_key=cfg.llm_api_key or "unused",
        base_url=cfg.llm_api_base,
        max_retries=_MAX_RETRIES,
    )
    return OpenAIModel(cfg.ai_model, provider=OpenAIProvider(openai_client=client))


_PROVIDER_BUILDERS: dict[str, Callable] = {
    "openai": _build_openai,
    "anthropic": _build_anthropic,
    "google": _build_google,
    "groq": _build_groq,
    "mistral": _build_mistral,
    "cohere": _build_cohere,
    "openai_compatible": _build_openai_compatible,
    "litellm": _build_litellm,
}


# ── Model factory ────────────────────────────────────────────────────

_PROVIDERS_WITHOUT_API_KEY = {"litellm"}


def _validated_site_config():
    """Load `SiteConfig` and assert the required LLM fields are populated."""
    from linkedin.models import SiteConfig

    cfg = SiteConfig.load()
    if not cfg.llm_api_key and cfg.llm_provider not in _PROVIDERS_WITHOUT_API_KEY:
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
