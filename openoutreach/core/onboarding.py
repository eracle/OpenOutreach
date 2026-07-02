# openoutreach/core/onboarding.py
"""Onboarding for the email-only funnel — Campaign + operator account + LLM +
BetterContact + a sending mailbox.

Order (interactive): product & objective → LLM → **mailbox** → **BetterContact**
→ country → newsletter/legal. The two credential steps are mandatory and
imperative (paste → auth-check → store); everything else is a declarative wizard
question. The operator's email is **not asked** — it is the connected mailbox's
address, so the account is created only after a mailbox exists.
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

from openoutreach.core.conf import (
    DEFAULT_EMAIL_DAILY_LIMIT,
    ROOT_DIR,
)

DEFAULT_PRODUCT_DOCS = ROOT_DIR / "README.md"
DEFAULT_CAMPAIGN_OBJECTIVE = ROOT_DIR / "docs" / "default_campaign.md"
DEFAULT_CAMPAIGN_NAME = "Email Outreach"

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config dataclass (declarative fields only — the credential steps write directly)
# ---------------------------------------------------------------------------

@dataclass
class OnboardConfig:
    """The declarative onboarding values — filled interactively or from JSON.

    The mailbox and BetterContact key are not here: they are captured and stored
    by their own imperative steps, and the operator email is read back from the
    stored mailbox.
    """

    product_description: str = ""
    campaign_objective: str = ""
    booking_link: str = ""
    country_code: str = ""
    llm_api_key: str = ""
    ai_model: str = ""
    llm_api_base: str = ""
    newsletter: bool = True
    legal_acceptance: bool = False


# ---------------------------------------------------------------------------
# State inspection
# ---------------------------------------------------------------------------

_CAMPAIGN_KEYS = {"product_description", "campaign_objective", "booking_link"}
_LLM_KEYS = {"llm_api_key", "ai_model", "llm_api_base"}
_JURISDICTION_KEYS = {"country_code", "newsletter", "legal_acceptance"}


def missing_keys() -> set[str]:
    """Return onboarding step keys that still need attention.

    Beyond the declarative fields, this reports the two credential steps
    (``mailbox``, ``bettercontact``) and the account itself, so the daemon knows
    onboarding is incomplete until every gate is satisfied.
    """
    from django.contrib.auth.models import User

    from openoutreach.core.models import Campaign, SiteConfig
    from openoutreach.emails import bettercontact
    from openoutreach.emails.models import has_mailbox

    keys: set[str] = set()

    if not Campaign.objects.exists():
        keys |= _CAMPAIGN_KEYS

    cfg = SiteConfig.load()
    if not cfg.llm_api_key:
        keys.add("llm_api_key")
    if not cfg.ai_model:
        keys.add("ai_model")
    # llm_api_base is only required for the openai_compatible provider.
    if cfg.ai_model.startswith("openai_compatible:") and not cfg.llm_api_base:
        keys.add("llm_api_base")

    if not has_mailbox():
        keys.add("mailbox")
    if not bettercontact.is_configured():
        keys.add("bettercontact")

    if not User.objects.filter(is_active=True, is_staff=True).exists():
        keys |= _JURISDICTION_KEYS

    return keys


# ---------------------------------------------------------------------------
# Interactive onboarding (ordered; needs a TTY)
# ---------------------------------------------------------------------------

def onboard_interactive() -> None:
    """Run the ordered onboarding steps and persist everything.

    Idempotent per step: an already-satisfied step is skipped, so a partial
    onboarding resumes where it left off. Raises SystemExit if the user cancels.
    """
    from openoutreach.core.onboarding_prompts import (
        CAMPAIGN_QUESTIONS,
        JURISDICTION_QUESTIONS,
        LLM_QUESTIONS,
    )
    from openoutreach.core.onboarding_wizard import ask

    missing = missing_keys()
    answers: dict = {}

    # 1–2 product & objective, 3 LLM
    for question in CAMPAIGN_QUESTIONS + LLM_QUESTIONS:
        if question.key in missing:
            collected = ask([question])
            if collected is None:
                raise SystemExit("Onboarding cancelled.")
            answers.update(collected)
    _verify_llm_answers(answers)

    # 4 mailbox, 5 BetterContact (mandatory, imperative)
    if "mailbox" in missing:
        _setup_mailbox_interactive()
    if "bettercontact" in missing:
        _setup_bettercontact_interactive()

    # 6 country, 7 newsletter + legal
    for question in JURISDICTION_QUESTIONS:
        if question.key not in missing:
            continue
        # The newsletter opt-in default is jurisdiction-aware: off in GDPR/opt-in
        # countries (no consent-by-silence), on elsewhere. Country was collected
        # just above (or already on SiteConfig), so it's available here.
        if question.key == "newsletter":
            question.default = not _newsletter_default_off(answers)
        collected = ask([question])
        if collected is None:
            raise SystemExit("Onboarding cancelled.")
        answers.update(collected)

    apply(OnboardConfig(**{
        k: v for k, v in answers.items() if k in OnboardConfig.__dataclass_fields__
    }))


def _newsletter_default_off(answers: dict) -> bool:
    """Whether the newsletter opt-in should default to OFF for this operator.

    True in GDPR/opt-in jurisdictions (no consent-by-silence). Reads the country
    just collected, falling back to whatever is already on ``SiteConfig``.
    """
    from openoutreach.core.geo import is_gdpr_protected
    from openoutreach.core.models import SiteConfig

    country_code = answers.get("country_code") or SiteConfig.load().country_code
    return is_gdpr_protected(country_code)


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


# ── Mailbox step (mandatory) ─────────────────────────────────────

_PASTE_GUIDANCE = """\
  Connect a sending mailbox you own (Gmail / Workspace / your own domain).
  Paste the App-Passwords sheet (columns: Email, App Password) with its header,
  then Ctrl+D to submit. Each box is auth-checked before it's saved; at least one
  must succeed to continue.
"""


def _setup_mailbox_interactive() -> None:
    """Paste app passwords → auth-check → store. Loops until ≥1 box authenticates."""
    import questionary
    from openoutreach.emails.mailbox_setup import import_mailboxes
    from openoutreach.emails.models import has_mailbox

    from openoutreach.core.onboarding_wizard import MultilineText, _BACK

    while not has_mailbox():
        print(_PASTE_GUIDANCE)
        pasted = MultilineText("mailboxes", "Paste your App-Passwords sheet").ask("")
        if pasted is None:
            raise SystemExit("Onboarding cancelled.")
        if pasted == _BACK or not pasted.strip():
            continue
        try:
            report = import_mailboxes(pasted, DEFAULT_EMAIL_DAILY_LIMIT)
        except ValueError as exc:
            questionary.print(f"  {exc}", style="fg:red")
            continue
        for email, reason in report.failures:
            questionary.print(f"  ✗ {email}: {reason}", style="fg:red")
        if report.stored:
            questionary.print(
                f"  ✓ {report.stored} mailbox(es) authenticated and saved.", style="fg:green",
            )
        else:
            questionary.print(
                "  No mailbox authenticated — check the app passwords and try again.",
                style="fg:red",
            )


# ── BetterContact step (mandatory) ───────────────────────────────

def _setup_bettercontact_interactive() -> None:
    """Capture the BetterContact API key. Loops until a non-empty key is set.

    BetterContact powers both Lead Finder discovery and email enrichment, so
    without it there is no pipeline — the key is required, not an upsell.
    """
    import questionary
    from openoutreach.core.models import SiteConfig
    from openoutreach.emails import bettercontact
    from openoutreach.core.onboarding_wizard import Password, _BACK

    print(
        "  BetterContact powers lead discovery and email finding "
        "(https://bettercontact.rocks?fpr=openoutreach)."
    )
    while not bettercontact.is_configured():
        key = Password("bettercontact_api_key", "BetterContact API key").ask("")
        if key is None:
            raise SystemExit("Onboarding cancelled.")
        if key == _BACK or not key.strip():
            continue
        cfg = SiteConfig.load()
        cfg.bettercontact_api_key = key.strip()
        cfg.save()
        questionary.print("  ✓ BetterContact key saved.", style="fg:green")


# ---------------------------------------------------------------------------
# Record creation (pure DB, no console I/O)
# ---------------------------------------------------------------------------

def _read_default_file(path) -> str:
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def _create_campaign(product_docs: str, objective: str, booking_link: str = ""):
    """Create the Campaign record and return it."""
    from openoutreach.core.models import Campaign

    campaign = Campaign.objects.create(
        name=DEFAULT_CAMPAIGN_NAME,
        product_docs=product_docs,
        campaign_objective=objective,
        booking_link=booking_link,
    )
    logger.info("Campaign '%s' created!", DEFAULT_CAMPAIGN_NAME)
    return campaign


def _create_account(campaign, email: str):
    """Create the operator Django ``User`` from the connected mailbox email.

    Identity lives entirely on the ``User`` (email + handle); the only persisted
    operator setting is ``SiteConfig.country_code``, written by ``apply()``.
    """
    from django.contrib.auth.models import User

    handle = email.split("@")[0].lower().replace(".", "_").replace("+", "_")

    user, created = User.objects.get_or_create(
        username=handle,
        defaults={"is_staff": True, "is_active": True, "email": email},
    )
    if created:
        user.set_unusable_password()
        user.save()

    campaign.users.add(user)
    logger.info("Operator account '%s' created (email=%s)", handle, email)
    return user


def _subscribe_newsletter_once(email: str, opted_in: bool) -> None:
    """Subscribe the operator's mailbox email to the newsletter, once.

    Honors the operator's explicit answer: an opt-in is lawful consent in any
    jurisdiction, so a yes always subscribes. Country only drives the *default*
    of the newsletter question (off in GDPR/opt-in jurisdictions), never an
    override that blocks an explicit yes. Nothing is stored; runs once at account
    creation.
    """
    from openoutreach.emails.newsletter import subscribe_to_newsletter

    if opted_in and email and "@" in email:
        subscribe_to_newsletter(email)


# ---------------------------------------------------------------------------
# Single write path
# ---------------------------------------------------------------------------

def apply(config: OnboardConfig) -> None:
    """Idempotent: create missing Campaign, LLM config, and operator account.

    The mailbox and BetterContact key are already stored by their own steps; this
    reads the operator email back from the mailbox, persists the country on
    ``SiteConfig``, and subscribes the operator to the newsletter once (a
    country-dependent, one-time action — no stored flag).
    """
    from django.contrib.auth.models import User

    from openoutreach.core.models import Campaign, SiteConfig
    from openoutreach.emails.models import Mailbox

    # Campaign
    campaign = Campaign.objects.first()
    if campaign is None and config.product_description:
        campaign = _create_campaign(
            product_docs=config.product_description or _read_default_file(DEFAULT_PRODUCT_DOCS),
            objective=config.campaign_objective or _read_default_file(DEFAULT_CAMPAIGN_OBJECTIVE),
            booking_link=config.booking_link,
        )

    # LLM config → DB
    cfg = SiteConfig.load()
    updated = False
    for field_name, val in [
        ("llm_api_key", config.llm_api_key),
        ("ai_model", config.ai_model),
        ("llm_api_base", config.llm_api_base),
    ]:
        if val:
            setattr(cfg, field_name, val)
            updated = True
    if updated:
        cfg.save()
        logger.info("LLM config saved to database.")

    # Country → SiteConfig (the only persisted operator setting).
    country_code = (config.country_code or "").strip().lower()
    if country_code and cfg.country_code != country_code:
        cfg.country_code = country_code
        cfg.save(update_fields=["country_code"])

    # Operator account — email comes from the connected mailbox.
    if not User.objects.filter(is_active=True, is_staff=True).exists() and campaign is not None:
        box = Mailbox.objects.first()
        if box is None:
            raise SystemExit("A sending mailbox is required before onboarding can finish.")
        _create_account(campaign, box.from_address)
        _subscribe_newsletter_once(box.from_address, config.newsletter)
