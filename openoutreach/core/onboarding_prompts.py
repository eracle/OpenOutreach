"""Self-hosted onboarding prompt definitions (email-only funnel).

The declarative questions, grouped in onboarding order. The two credential steps
that aren't simple questions — connecting a mailbox and setting the BetterContact
key — are imperative (paste → auth-check → store) and live in ``onboarding.py``;
they slot in between the LLM group and the country group.
"""

from __future__ import annotations

from openoutreach.core.onboarding_wizard import (
    Confirm,
    MultilineText,
    Password,
    Text,
)

# ── Campaign (what you sell, and to whom) ────────────────────────

PRODUCT_DESCRIPTION = MultilineText("product_description", "Product/service description")
CAMPAIGN_OBJECTIVE = MultilineText(
    "campaign_objective",
    "Campaign objective (e.g. 'sell analytics platform to CTOs')",
)
BOOKING_LINK = Text("booking_link", "Booking link (e.g. https://cal.com/you)", required=False)

# ── LLM ──────────────────────────────────────────────────────────

# The model carries its provider as a pydantic-ai `provider:model` identifier —
# one field, so the key and the provider can never disagree (the bug that sent an
# sk-ant- key to OpenAI). Valid providers: openai, anthropic, google, groq,
# mistral, cohere, openai_compatible.
AI_MODEL = Text(
    "ai_model",
    "AI model — you must prefix the provider, written as 'provider:model' "
    "(e.g. anthropic:claude-sonnet-4-5-20250929, openai:gpt-4o, groq:llama-3.3-70b). "
    "Valid providers: openai, anthropic, google, groq, mistral, cohere, openai_compatible",
)
LLM_API_KEY = Password("llm_api_key", "LLM API key for that provider (e.g. sk-...)")
LLM_API_BASE = Text(
    "llm_api_base",
    "LLM API base URL (only for openai_compatible:* models — OpenRouter / Together / Ollama / vLLM)",
    required=False,
)

# ── Country (timezone + email jurisdiction) ──────────────────────

COUNTRY = Text(
    "country_code",
    "Your country (ISO 3166 alpha-2, e.g. US, GB, DE) — sets your active-hours "
    "timezone and email-jurisdiction defaults",
)

# ── Preferences + legal ──────────────────────────────────────────

NEWSLETTER = Confirm("newsletter", "Subscribe to OpenOutreach newsletter?", default=True)
# contribute_to_hub is NOT asked — it is derived from the operator's country
# (collected above) at account creation (apply_gdpr_contribution_override in
# core/geo.py).
LEGAL = Confirm(
    "legal_acceptance",
    "Do you accept the Legal Notice? (https://github.com/eracle/OpenOutreach/LEGAL_NOTICE.md)",
    default=False,
    required=True,
)

# ── Ordered groups (imperative mailbox + BetterContact steps run between
#    the LLM group and the country group — see onboarding.py) ──────
CAMPAIGN_QUESTIONS = [PRODUCT_DESCRIPTION, CAMPAIGN_OBJECTIVE, BOOKING_LINK]
LLM_QUESTIONS = [AI_MODEL, LLM_API_KEY, LLM_API_BASE]
JURISDICTION_QUESTIONS = [COUNTRY, NEWSLETTER, LEGAL]
