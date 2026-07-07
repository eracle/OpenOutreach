# openoutreach/core/onboarding.py
"""Email-first onboarding as an ordered list of idempotent steps.

First principles
----------------
Onboarding is a **sequence of independent steps**. Each step knows two things:

  * ``is_done()`` — is this already satisfied? (reads the DB, never prompts)
  * ``run()``     — collect what's missing and **persist it immediately**

The runner executes only the steps whose ``is_done()`` is false, in order. Because
every step persists the moment it succeeds, a crash or Ctrl+C mid-onboarding
resumes exactly where it stopped, and a satisfied step is never revisited.

Why this shape kills the "SMTP onboarding keeps looping back" bug:

  * The **only** thing that decides ordering is ``is_done()``. Once a mailbox
    exists, ``mailbox`` is done — the runner cannot land back on it.
  * A step's ``run()`` owns its **own** retry loop. A credential that fails
    verification re-asks *that step's* fields (with what you typed retained) —
    it never rewinds to an earlier step, and never restarts the whole wizard.
  * There is no end-of-wizard ``apply()`` that could half-fail and strand state:
    each step is its own commit point.

Cancellation is a single exception, not a return value threaded through every
caller: the wizard prompts return ``None`` on Ctrl+C, ``_required()`` turns that
into ``OnboardingCancelled`` at one boundary, and steps that want to treat cancel
specially (the mailbox, once one box exists) catch it.

Order: campaign → LLM (live-verified) → **mailbox** (SMTP auth-checked) →
**BetterContact key** → account (country + newsletter + legal, then the operator
``User`` created from the connected mailbox's address). The mailbox and the
BetterContact key are mandatory — BetterContact powers both discovery and
enrichment, and the operator's email is *not asked*: it is the mailbox's
``from_address``, so the account is the last step, after a mailbox exists.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, TypeVar

from openoutreach.core import onboarding_wizard as wiz
from openoutreach.core.conf import DEFAULT_EMAIL_DAILY_LIMIT

logger = logging.getLogger(__name__)

DEFAULT_CAMPAIGN_NAME = "Email Outreach"

_T = TypeVar("_T")


class OnboardingCancelled(SystemExit):
    """Raised when the operator cancels (Ctrl+C) a step that isn't yet satisfied."""

    def __init__(self) -> None:
        super().__init__("Onboarding cancelled.")


def _required(answer: _T | None) -> _T:
    """Unwrap a wizard answer, aborting onboarding when the operator cancelled.

    Wizard prompts return ``None`` on Ctrl+C; every mandatory answer is passed
    through here so cancellation raises once, instead of a ``None`` check after
    each prompt.
    """
    if answer is None:
        raise OnboardingCancelled
    return answer


def _say(message: str, style: str) -> None:
    """Print a styled status line (green ✓, red ✗, cyan progress)."""
    import questionary

    questionary.print(message, style=style)


# ---------------------------------------------------------------------------
# Step registry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Step:
    """One onboarding step: a name, a done-check, and a runner."""

    key: str
    is_done: Callable[[], bool]
    run: Callable[[], None]


# ── Campaign: what you sell, and to whom ─────────────────────────

def _campaign_done() -> bool:
    from openoutreach.core.models import Campaign

    return Campaign.objects.exists()


def _run_campaign() -> None:
    from openoutreach.core.models import Campaign

    print("\n  Campaign — describe what you sell and who you're selling to.")
    Campaign.objects.create(
        name=DEFAULT_CAMPAIGN_NAME,
        product_docs=_required(wiz.multiline("Product/service description")),
        campaign_objective=_required(
            wiz.multiline("Campaign objective (e.g. 'sell analytics platform to CTOs')")
        ),
        booking_link=_required(wiz.text("Booking link (e.g. https://cal.com/you)", required=False)),
    )
    logger.info("Campaign '%s' created.", DEFAULT_CAMPAIGN_NAME)


# ── LLM: the agent's brain (live-verified) ───────────────────────

_AI_MODEL_PROMPT = (
    "AI model — prefix the provider as 'provider:model' "
    "(e.g. anthropic:claude-sonnet-4-5-20250929, openai:gpt-4o, groq:llama-3.3-70b). "
    "Providers: openai, anthropic, google, groq, mistral, cohere, openai_compatible"
)


def _llm_done() -> bool:
    from openoutreach.core.models import SiteConfig

    cfg = SiteConfig.load()
    if not cfg.llm_api_key or not cfg.ai_model:
        return False
    # openai_compatible:* has no default endpoint — it needs an explicit base URL.
    if cfg.ai_model.startswith("openai_compatible:") and not cfg.llm_api_base:
        return False
    return True


def _run_llm() -> None:
    from openoutreach.core.llm import verify_llm_credentials

    print("\n  LLM — the model that qualifies leads and writes your emails.")
    model = base = ""
    while True:
        model = _required(wiz.text(_AI_MODEL_PROMPT, default=model))
        key = _required(wiz.text("API key for that provider (e.g. sk-...)", secret=True))
        base = _required(wiz.text(
            "API base URL (only for openai_compatible:* — OpenRouter / Together / Ollama / vLLM)",
            default=base, required=False,
        ))

        _say("  Verifying LLM credentials…", "fg:cyan")
        error = verify_llm_credentials(model, key, base)
        if error is None:
            _save_llm(model, key, base)
            _say("  ✓ LLM credentials OK.", "fg:green")
            return
        _say(f"  ✗ {error}", "fg:red")


def _save_llm(model: str, key: str, base: str) -> None:
    from openoutreach.core.models import SiteConfig

    cfg = SiteConfig.load()
    cfg.ai_model, cfg.llm_api_key, cfg.llm_api_base = model, key, base
    cfg.save()
    logger.info("LLM config saved.")


# ── Mailbox: the address you send from (SMTP auth-checked) ────────

_MAILBOX_GUIDANCE = """
  Mailbox — connect a sending inbox you own (IceMail / Gmail / Workspace / your
  own domain). Enter its fields one at a time; use the *app password*, not the
  login password. The box is auth-checked over SMTP before it's saved — nothing
  is stored unless the login succeeds. Ports: 587 (STARTTLS) or 465 (SSL).
"""

_MAILBOX_DEFAULTS = {
    "from_address": "", "host": "smtp.gmail.com", "port": 587,
    "imap_host": "imap.gmail.com", "imap_port": 993,
}


def _mailbox_done() -> bool:
    from openoutreach.emails.models import has_mailbox

    return has_mailbox()


def _run_mailbox() -> None:
    from openoutreach.emails.models import Mailbox, has_mailbox

    print(_MAILBOX_GUIDANCE)
    entry = dict(_MAILBOX_DEFAULTS)
    while True:
        try:
            entry = _prompt_mailbox_fields(entry)  # retained for retry / next box
        except OnboardingCancelled:
            # Cancelling once a box exists just stops adding more; with no box the
            # step is unsatisfiable, so onboarding genuinely aborts.
            if has_mailbox():
                return
            raise

        box, reason = Mailbox.objects.create_verified(**entry, daily_limit=DEFAULT_EMAIL_DAILY_LIMIT)
        if box is None:
            _say(f"  ✗ {entry['from_address']}: {reason}", "fg:red")
            continue  # re-ask with the same values pre-filled — never rewinds
        _say(f"  ✓ {box.from_address} authenticated and saved.", "fg:green")

        if not wiz.confirm("Connect another mailbox?", default=False):
            return  # False, or None (Ctrl+C) — either way we already have a box


def _looks_like_email(value: str) -> bool | str:
    return True if "@" in value else "That doesn't look like an email address."


def _prompt_mailbox_fields(entry: dict) -> dict:
    """Ask the six mailbox fields, seeded from *entry*. Raises on cancel."""
    return {
        "from_address": _required(wiz.text(
            "Email address (the From: address and SMTP login)",
            default=entry["from_address"], validate=_looks_like_email,
        )),
        "password": _required(wiz.text("App password", secret=True)),
        "host": _required(wiz.text("SMTP host", default=entry["host"])),
        "port": _required(wiz.integer("SMTP port", default=entry["port"])),
        "imap_host": _required(wiz.text("IMAP host", default=entry["imap_host"])),
        "imap_port": _required(wiz.integer("IMAP port", default=entry["imap_port"])),
    }


# ── BetterContact: powers discovery + enrichment (mandatory) ──────

def _bettercontact_done() -> bool:
    from openoutreach.emails import bettercontact

    return bettercontact.is_configured()


def _run_bettercontact() -> None:
    from openoutreach.core.models import SiteConfig

    print(
        "\n  BetterContact — powers lead discovery and email finding "
        "(https://bettercontact.rocks?fpr=openoutreach)."
    )
    cfg = SiteConfig.load()
    cfg.bettercontact_api_key = _required(wiz.text("BetterContact API key", secret=True))
    cfg.save()
    _say("  ✓ BetterContact key saved.", "fg:green")


# ── Account: country + newsletter + legal, then the operator User ─

def _account_done() -> bool:
    """Done only when an operator exists *and* its email matches the connected
    mailbox. Requiring the match (not merely 'a staff user exists') stops a legacy
    staff account with a blank/stale email from short-circuiting operator creation;
    the daemon's startup reconcile (`reconcile_operator_email`) keeps it true after."""
    from django.contrib.auth.models import User

    from openoutreach.emails.models import Mailbox

    box = Mailbox.objects.first()
    if box is None:
        return False
    return User.objects.filter(
        is_active=True, is_staff=True, email=box.from_address
    ).exists()


def _run_account() -> None:
    """Collect jurisdiction, gate on the Legal Notice, then create the operator.

    Nothing is persisted until every answer is in and the Legal Notice is
    accepted, so a declined/cancelled step leaves no partial state behind.
    """
    from openoutreach.core.geo import is_gdpr_protected

    country = _required(wiz.text(
        "Your country (ISO 3166 alpha-2, e.g. US, GB, DE) — sets your active-hours "
        "timezone and email-jurisdiction defaults",
        validate=_looks_like_country,
    )).lower()

    # Newsletter opt-in defaults OFF in GDPR/opt-in jurisdictions (no consent by
    # silence), ON elsewhere. An explicit yes is lawful consent anywhere.
    newsletter = _required(wiz.confirm(
        "Subscribe to the OpenOutreach newsletter?",
        default=not is_gdpr_protected(country),
    ))
    _require_legal()
    _finalize_account(country, newsletter)


def _looks_like_country(value: str) -> bool | str:
    if len(value) == 2 and value.isalpha():
        return True
    return "Enter a 2-letter country code (e.g. US, GB, DE)."


def _require_legal() -> None:
    """Gate onboarding on Legal Notice acceptance; re-ask a decline, abort on cancel."""
    while True:
        accepted = wiz.confirm(
            "Do you accept the Legal Notice? "
            "(https://github.com/eracle/OpenOutreach/blob/main/LEGAL_NOTICE.md)",
            default=False,
        )
        if accepted is None:  # Ctrl+C
            raise OnboardingCancelled
        if accepted:
            return
        _say("  You must accept the Legal Notice to use OpenOutreach.", "fg:red")


def _finalize_account(country: str, newsletter: bool) -> None:
    """Persist country, create the operator ``User`` from the mailbox, subscribe once."""
    from openoutreach.core.models import Campaign, SiteConfig
    from openoutreach.emails.models import Mailbox
    from openoutreach.emails.newsletter import subscribe_to_newsletter

    box = Mailbox.objects.first()
    if box is None:  # the mailbox step runs before this one; defensive
        raise OnboardingCancelled

    cfg = SiteConfig.load()
    cfg.country_code = country
    cfg.save(update_fields=["country_code"])

    user = _create_operator(Campaign.objects.first(), box.from_address)
    if newsletter:
        subscribe_to_newsletter(box.from_address)
    logger.info("Operator account '%s' created (email=%s).", user.username, box.from_address)


def _create_operator(campaign, email: str):
    """Create the operator Django ``User`` from the connected mailbox address."""
    from django.contrib.auth.models import User

    handle = email.split("@")[0].lower().replace(".", "_").replace("+", "_")
    user, created = User.objects.get_or_create(
        username=handle,
        defaults={"is_staff": True, "is_active": True, "email": email},
    )
    if created:
        user.set_unusable_password()
        user.save()
    if campaign is not None:
        campaign.users.add(user)
    return user


# ---------------------------------------------------------------------------
# The ordered pipeline
# ---------------------------------------------------------------------------

STEPS: list[Step] = [
    Step("campaign", _campaign_done, _run_campaign),
    Step("llm", _llm_done, _run_llm),
    Step("mailbox", _mailbox_done, _run_mailbox),
    Step("bettercontact", _bettercontact_done, _run_bettercontact),
    Step("account", _account_done, _run_account),
]


def missing_keys() -> set[str]:
    """Return the keys of steps that still need attention (empty ⇒ fully onboarded)."""
    return {step.key for step in STEPS if not step.is_done()}


def onboard_interactive() -> None:
    """Run each unsatisfied step in order, persisting as it goes.

    Idempotent: an already-satisfied step is skipped, so a partial onboarding
    resumes where it left off. Raises ``OnboardingCancelled`` (a ``SystemExit``)
    if the operator cancels a step that isn't yet satisfiable.
    """
    for step in STEPS:
        if not step.is_done():
            step.run()
