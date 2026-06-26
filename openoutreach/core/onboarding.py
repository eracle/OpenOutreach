# openoutreach/core/onboarding.py
"""Onboarding: create Campaign + LinkedInProfile + LLM config in DB.

Two ways to supply config:
- OnboardConfig.from_json(path) — from a JSON file (non-interactive / cloud).
- collect_from_wizard()         — interactive questionary wizard (needs TTY).

Both return an OnboardConfig; ``apply()`` is the single write path.
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

from openoutreach.core.conf import (
    DEFAULT_CONNECT_DAILY_LIMIT,
    DEFAULT_FOLLOW_UP_DAILY_LIMIT,
    ROOT_DIR,
)

DEFAULT_PRODUCT_DOCS = ROOT_DIR / "README.md"
DEFAULT_CAMPAIGN_OBJECTIVE = ROOT_DIR / "docs" / "default_campaign.md"

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config dataclass (pure data — no I/O)
# ---------------------------------------------------------------------------

@dataclass
class OnboardConfig:
    """All values needed to onboard — filled interactively or from JSON."""

    linkedin_email: str = ""
    linkedin_password: str = ""
    campaign_name: str = ""
    product_description: str = ""
    campaign_objective: str = ""
    booking_link: str = ""
    seed_urls: str = ""
    llm_api_key: str = ""
    ai_model: str = ""
    llm_api_base: str = ""
    newsletter: bool = True
    contribute_to_hub: bool = True
    connect_daily_limit: int = DEFAULT_CONNECT_DAILY_LIMIT
    follow_up_daily_limit: int = DEFAULT_FOLLOW_UP_DAILY_LIMIT
    legal_acceptance: bool = False


# ---------------------------------------------------------------------------
# State inspection
# ---------------------------------------------------------------------------

_CAMPAIGN_KEYS = {
    "campaign_name", "product_description", "campaign_objective",
    "booking_link", "seed_urls",
}
_ACCOUNT_KEYS = {
    "linkedin_email", "linkedin_password", "newsletter", "contribute_to_hub",
    "connect_daily_limit", "follow_up_daily_limit",
    "legal_acceptance",
}
_LLM_KEYS = {"llm_api_key", "ai_model", "llm_api_base"}
_ALL_KEYS = _CAMPAIGN_KEYS | _ACCOUNT_KEYS | _LLM_KEYS


def missing_keys() -> set[str]:
    """Return onboarding field keys that still need values."""
    from openoutreach.core.models import Campaign, SiteConfig
    from openoutreach.linkedin.models import LinkedInProfile

    keys: set[str] = set()

    if not Campaign.objects.exists():
        keys |= _CAMPAIGN_KEYS

    if not LinkedInProfile.objects.filter(active=True).exists():
        keys |= _ACCOUNT_KEYS

    cfg = SiteConfig.load()
    if not cfg.llm_api_key:
        keys.add("llm_api_key")
    if not cfg.ai_model:
        keys.add("ai_model")
    # llm_api_base is only required for the openai_compatible provider, which is
    # written as the `openai_compatible:` prefix on the model identifier.
    if cfg.ai_model.startswith("openai_compatible:") and not cfg.llm_api_base:
        keys.add("llm_api_base")

    return keys


# ---------------------------------------------------------------------------
# Interactive collection (needs TTY)
# ---------------------------------------------------------------------------

def collect_from_wizard() -> OnboardConfig:
    """Run the questionary wizard for missing fields; return an OnboardConfig.

    Raises SystemExit if the user cancels.
    """
    from openoutreach.core.onboarding_prompts import SELF_HOSTED_QUESTIONS
    from openoutreach.core.onboarding_wizard import ask

    skip = _ALL_KEYS - missing_keys()
    questions = [q for q in SELF_HOSTED_QUESTIONS if q.key not in skip]
    if not questions or not any(q.required for q in questions):
        return OnboardConfig()

    answers = ask(questions)
    if answers is None:
        raise SystemExit("Onboarding cancelled.")

    _verify_llm_answers(answers)

    return OnboardConfig(**{
        k: v for k, v in answers.items()
        if k in OnboardConfig.__dataclass_fields__
    })


def _verify_llm_answers(answers: dict) -> None:
    """Live-check the collected LLM credentials, re-asking until they work.

    Mutates *answers* in place. No-op when the LLM fields weren't asked this run
    (already configured). Raises SystemExit if the user cancels the re-ask.
    """
    import questionary
    from openoutreach.core.llm import verify_llm_credentials
    from openoutreach.core.onboarding_prompts import AI_MODEL, LLM_API_BASE, LLM_API_KEY
    from openoutreach.core.onboarding_wizard import ask

    if "ai_model" not in answers and "llm_api_key" not in answers:
        return

    while True:
        questionary.print("  Verifying LLM credentials…", style="fg:cyan")
        error = verify_llm_credentials(
            answers.get("ai_model", ""),
            answers.get("llm_api_key", ""),
            answers.get("llm_api_base", ""),
        )
        if error is None:
            questionary.print("  ✓ LLM credentials OK.", style="fg:green")
            return

        questionary.print(f"  ✗ {error}", style="fg:red")
        retry = ask([AI_MODEL, LLM_API_KEY, LLM_API_BASE])
        if retry is None:
            raise SystemExit("Onboarding cancelled.")
        answers.update(retry)


# ---------------------------------------------------------------------------
# Record creation (pure DB, no I/O)
# ---------------------------------------------------------------------------

def _read_default_file(path) -> str:
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def _create_campaign(name: str, product_docs: str, objective: str, booking_link: str = ""):
    """Create a Campaign record and return it."""
    from openoutreach.core.models import Campaign

    campaign = Campaign.objects.create(
        name=name,
        product_docs=product_docs,
        campaign_objective=objective,
        booking_link=booking_link,
    )
    logger.info("Campaign '%s' created!", name)
    return campaign


def _create_account(
    campaign,
    email: str,
    password: str,
    *,
    subscribe: bool = True,
    contribute_to_hub: bool = True,
    connect_daily: int = DEFAULT_CONNECT_DAILY_LIMIT,
    follow_up_daily: int = DEFAULT_FOLLOW_UP_DAILY_LIMIT,
):
    """Create a User + LinkedInProfile record and return the profile."""
    from django.contrib.auth.models import User
    from openoutreach.linkedin.models import LinkedInProfile

    handle = email.split("@")[0].lower().replace(".", "_").replace("+", "_")

    user, created = User.objects.get_or_create(
        username=handle,
        defaults={"is_staff": True, "is_active": True},
    )
    if created:
        user.set_unusable_password()
        user.save()

    campaign.users.add(user)

    profile = LinkedInProfile.objects.create(
        user=user,
        linkedin_username=email,
        linkedin_password=password,
        subscribe_newsletter=subscribe,
        contribute_to_hub=contribute_to_hub,
        connect_daily_limit=connect_daily,
        follow_up_daily_limit=follow_up_daily,
    )

    logger.info("Account '%s' created! (email=%s)", handle, email)
    return profile


def _create_seed_leads(campaign, seed_urls: str) -> None:
    """Parse seed URL text and create QUALIFIED leads."""
    if not seed_urls or not seed_urls.strip():
        return
    from openoutreach.linkedin.setup.seeds import parse_seed_urls, create_seed_leads

    public_ids = parse_seed_urls(seed_urls)
    if public_ids:
        created = create_seed_leads(campaign, public_ids)
        logger.info("%d seed profile(s) added as QUALIFIED.", created)


# ---------------------------------------------------------------------------
# Single write path
# ---------------------------------------------------------------------------

def apply(config: OnboardConfig) -> None:
    """Idempotent: create missing Campaign, Account, env vars, and legal acceptance."""
    from openoutreach.core.management.setup_crm import DEFAULT_CAMPAIGN_NAME
    from openoutreach.core.models import Campaign
    from openoutreach.linkedin.models import LinkedInProfile

    # Campaign
    campaign = Campaign.objects.first()
    if campaign is None and config.campaign_name:
        campaign = _create_campaign(
            name=config.campaign_name or DEFAULT_CAMPAIGN_NAME,
            product_docs=config.product_description or _read_default_file(DEFAULT_PRODUCT_DOCS),
            objective=config.campaign_objective or _read_default_file(DEFAULT_CAMPAIGN_OBJECTIVE),
            booking_link=config.booking_link,
        )
        _create_seed_leads(campaign, config.seed_urls)

    # Account
    if (
        not LinkedInProfile.objects.filter(active=True).exists()
        and config.linkedin_email
    ):
        _create_account(
            campaign,
            config.linkedin_email,
            config.linkedin_password,
            subscribe=config.newsletter,
            contribute_to_hub=config.contribute_to_hub,
            connect_daily=config.connect_daily_limit,
            follow_up_daily=config.follow_up_daily_limit,
        )

    # LLM config → DB
    from openoutreach.core.models import SiteConfig
    cfg = SiteConfig.load()
    updated = False
    for field, val in [
        ("llm_api_key", config.llm_api_key),
        ("ai_model", config.ai_model),
        ("llm_api_base", config.llm_api_base),
    ]:
        if val:
            setattr(cfg, field, val)
            updated = True
    if updated:
        cfg.save()
        logger.info("LLM config saved to database.")

    # Legal
    if config.legal_acceptance:
        from openoutreach.linkedin.models import LinkedInProfile as LP
        LP.objects.filter(legal_accepted=False, active=True).update(legal_accepted=True)
