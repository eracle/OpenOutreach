"""Self-hosted onboarding prompt definitions.

Vendored from the (retired) openoutreach-cli. Only the self-hosted profile
is kept — the cloud-only VPN questions and their geo lookups are dropped.
"""

from __future__ import annotations

from openoutreach.core.onboarding_wizard import (
    Autocomplete,
    Confirm,
    IntText,
    MultilineText,
    Password,
    Text,
)

# ── Campaign ─────────────────────────────────────────────────────

CAMPAIGN_NAME = Text("campaign_name", "Campaign name", default="LinkedIn Outreach")
PRODUCT_DESCRIPTION = MultilineText("product_description", "Product/service description")
CAMPAIGN_OBJECTIVE = MultilineText(
    "campaign_objective",
    "Campaign objective (e.g. 'sell analytics platform to CTOs')",
)
BOOKING_LINK = Text("booking_link", "Booking link (e.g. https://cal.com/you)", required=False)
SEED_URLS = MultilineText(
    "seed_urls", "LinkedIn seed profile URLs (one per line)", required=False,
)

# ── LinkedIn account ─────────────────────────────────────────────

LINKEDIN_EMAIL = Text("linkedin_email", "LinkedIn email")
LINKEDIN_PASSWORD = Password("linkedin_password", "LinkedIn password")

# ── LLM ──────────────────────────────────────────────────────────

LLM_PROVIDERS = [
    "openai", "anthropic", "google", "groq", "mistral", "cohere", "openai_compatible",
]
LLM_PROVIDER = Autocomplete(
    "llm_provider", "LLM provider",
    resolver=lambda _: LLM_PROVIDERS, default="openai",
)
LLM_API_KEY = Password("llm_api_key", "LLM API key (e.g. sk-...)")
AI_MODEL = Text("ai_model", "AI model (e.g. gpt-4o, claude-sonnet-4-5-20250929)")
LLM_API_BASE = Text(
    "llm_api_base",
    "LLM API base URL (only for openai_compatible — OpenRouter / Together / Ollama / vLLM)",
    required=False,
)

# ── Preferences ──────────────────────────────────────────────────

NEWSLETTER = Confirm("newsletter", "Subscribe to OpenOutreach newsletter?", default=True)
CONTRIBUTE_TO_HUB = Confirm(
    "contribute_to_hub",
    "Contribute resolved non-EU prospect contacts to the shared store (earns lookup credits)?",
    default=True,
)
CONNECT_DAILY = IntText("connect_daily_limit", "Connection requests daily limit", default=50)
CONNECT_WEEKLY = IntText("connect_weekly_limit", "Connection requests weekly limit", default=250)
FOLLOW_UP_DAILY = IntText("follow_up_daily_limit", "Follow-up messages daily limit", default=100)

# ── Legal ────────────────────────────────────────────────────────

LEGAL = Confirm(
    "legal_acceptance",
    "Do you accept the Legal Notice? (https://github.com/eracle/OpenOutreach/LEGAL_NOTICE.md)",
    default=False,
    required=True,
)

# ── Profile ──────────────────────────────────────────────────────

SELF_HOSTED_QUESTIONS = [
    CAMPAIGN_NAME, PRODUCT_DESCRIPTION, CAMPAIGN_OBJECTIVE, BOOKING_LINK,
    SEED_URLS,
    LINKEDIN_EMAIL, LINKEDIN_PASSWORD,
    LLM_PROVIDER, LLM_API_KEY, AI_MODEL, LLM_API_BASE,
    NEWSLETTER, CONTRIBUTE_TO_HUB,
    CONNECT_DAILY, CONNECT_WEEKLY, FOLLOW_UP_DAILY,
    LEGAL,
]
