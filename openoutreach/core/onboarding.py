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
**BetterContact key** → account (your email + country + newsletter + legal, then
the operator ``User``). The mailbox and the BetterContact key are mandatory —
BetterContact powers both discovery and enrichment. The account step asks the
operator's **own email** (the human's inbox — BCC-copy + newsletter target),
deliberately distinct from the mailbox ``from_address``; the ``User`` is created
last, after a mailbox exists.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, TypeVar

from openoutreach.core import onboarding_wizard as wiz
from openoutreach.core.conf import DEFAULT_EMAIL_DAILY_LIMIT

logger = logging.getLogger(__name__)

DEFAULT_CAMPAIGN_NAME = "Email Outreach"

_INTRO = """
  Welcome to OpenOutreach — a self-hosted AI sales agent that runs your cold
  email funnel end to end: define your ICP, discover leads, qualify them, find a
  verified work email, then send and follow up from a mailbox you own.

  Setup takes a few minutes. Have three things ready:
    • an LLM provider key — the agent qualifies leads and writes your emails
    • a mailbox you own — Gmail, Workspace, or any SMTP inbox
    • a BetterContact key — powers lead discovery and email finding (free tier to start)

  OpenOutreach is free; you pay only the providers above. Stop anytime — setup
  resumes where you left off.
"""

# Plain-language summary of the two funding behaviours (Legal Notice §4 and §6),
# shown at onboarding so they are seen up front rather than only via the link.
_INFORMATION_NOTICE = """
  ── Before you accept: how OpenOutreach funds itself ──

  OpenOutreach is free and open source. Two behaviours help sustain it. Both are
  covered in full by the Legal Notice (sections 4 and 6) — in plain terms:

  1. Freemium promotional campaign — only if you run a freemium campaign.
     A fraction of your sending goes to a maintainer-configured campaign that
     advertises OpenOutreach itself — sent from your own mailbox, under your
     sending identity (as if from you), to recipients unrelated to your leads.
     Your own qualified leads are never used for it. This has been part of the
     project since the beginning. Disable it by editing the source.

  2. Shared contacts store (the hub).
     When a paid lookup resolves a work email, a minimal record — the profile URL,
     country, the email (and, if enabled, an on-device profile vector) — is
     contributed to a shared store, so other operators can skip paying for a contact
     you already resolved and you can resolve theirs for free. Outside the EU/EEA, UK
     and Switzerland this is on by default; inside, it is opt-out. Disable it by
     editing the source, or turn off contribution later in the Django Admin.

  Full detail, your responsibilities, and how to opt out are in the Legal Notice.
"""

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

    print(
        "\n  Campaign — describe what you sell and who you're selling to. This\n"
        "  trains the qualifier (which leads are a fit) and briefs the email agent\n"
        "  (how to pitch). Be specific — a vague target yields vague targeting."
    )
    Campaign.objects.create(
        name=DEFAULT_CAMPAIGN_NAME,
        product_docs=_required(wiz.multiline(
            "Product/service description — what it does, who it's for, the problem it solves "
            "(e.g. 'A self-hosted CI dashboard for small dev teams — replaces spreadsheet "
            "build-tracking; cuts flaky-test triage from hours to minutes')"
        )),
        campaign_target=_required(wiz.multiline(
            "Campaign target — who you're going after and the outcome you want "
            "(e.g. 'book demos with CTOs at Series-A SaaS')"
        )),
        booking_link=_required(wiz.text(
            "Booking link the email agent can share (e.g. https://cal.com/you) — optional",
            required=False,
        )),
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
  Mailbox — connect a sending inbox you own. This is the address your outreach
  goes out From:, so use a real inbox you can send and receive on. Enter its
  fields one at a time, and use an *app password*, not your login password. The
  box is auth-checked over SMTP before anything is saved — nothing is stored
  unless the login succeeds.

  No good sending box yet? IceMail sets up a warmed, ready-to-send Google
  Workspace inbox in minutes (~$2.50/mo): https://icemail.ai?via=openoutreach

  The SMTP/IMAP host + port fields below default to Gmail / Google Workspace
  (smtp.gmail.com:587, imap.gmail.com:993). If you're on Gmail, Workspace, or
  IceMail, just press Enter through them. For any other provider, type its own
  SMTP/IMAP hosts. Ports: 587 (STARTTLS) or 465 (SSL).
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


def _prompt_mailbox_fields(entry: dict) -> dict:
    """Ask the six mailbox fields, seeded from *entry*. Raises on cancel."""
    return {
        "from_address": _required(wiz.text(
            "Email address (your From: address and SMTP username)",
            default=entry["from_address"], validate=_looks_like_email,
        )),
        "password": _required(wiz.text(
            "App password (Gmail/Workspace: myaccount.google.com → Security → "
            "App passwords — NOT your login password)",
            secret=True,
        )),
        "host": _required(wiz.text("SMTP host (Enter = Gmail/Workspace)", default=entry["host"])),
        "port": _required(wiz.integer("SMTP port (587 STARTTLS / 465 SSL)", default=entry["port"])),
        "imap_host": _required(wiz.text("IMAP host (Enter = Gmail/Workspace)", default=entry["imap_host"])),
        "imap_port": _required(wiz.integer("IMAP port", default=entry["imap_port"])),
    }


# ── BetterContact: powers discovery + enrichment (mandatory) ──────

def _bettercontact_done() -> bool:
    from openoutreach.emails import bettercontact

    return bettercontact.is_configured()


def _run_bettercontact() -> None:
    from openoutreach.core.models import SiteConfig

    print(
        "\n  BetterContact — the one paid step, and it does double duty: lead\n"
        "  DISCOVERY (ICP search — billed nothing) and email FINDING (one credit\n"
        "  per verified work email, top-ranked leads only). Free tier is ~50\n"
        "  lookups to start.\n\n"
        "  Get a key (affiliate link — supports OpenOutreach, no markup to you):\n"
        "  https://bettercontact.rocks?fpr=openoutreach\n"
        "  Then copy your API key from the dashboard and paste it below."
    )
    cfg = SiteConfig.load()
    cfg.bettercontact_api_key = _required(wiz.text("BetterContact API key", secret=True))
    cfg.save()
    _say("  ✓ BetterContact key saved.", "fg:green")


# ── Account: country + newsletter + information notice + legal, then the operator User ─

def _account_done() -> bool:
    """Done only when an operator exists *with a non-blank email* — the operator's
    own inbox, where send-copies (BCC) and the newsletter go. Requiring a real
    email (not merely 'a staff user exists') stops a legacy blank-email account
    from short-circuiting the address prompt."""
    from django.contrib.auth.models import User

    return User.objects.filter(is_active=True, is_staff=True).exclude(email="").exists()


def _run_account() -> None:
    """Collect jurisdiction, show the funding-behaviour notice, gate on the Legal
    Notice, then create the operator.

    Nothing is persisted until every answer is in and the Legal Notice is
    accepted, so a declined/cancelled step leaves no partial state behind.
    """
    from openoutreach.core.geo import is_gdpr_protected

    # The operator's own inbox — where we BCC a copy of every send and, if opted
    # in, subscribe them to the newsletter. Deliberately NOT the mailbox
    # from_address: that is the sending robot, this is the human reading the copies.
    operator_email = _required(wiz.text(
        "Your email address — we'll BCC you a copy of every outreach email sent, "
        "and (if you opt in below) send product updates here. This is your own "
        "inbox, not the sending mailbox.",
        validate=_looks_like_email,
    )).strip()

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
    _say(_INFORMATION_NOTICE, "fg:yellow")
    _require_legal()
    _finalize_account(operator_email, country, newsletter)


def _looks_like_country(value: str) -> bool | str:
    """Validate an ISO 3166-1 alpha-2 code against the same table active-hours uses.

    ``pytz.country_timezones`` is the country→zone authority ``timezone_for_country``
    reads; validating against it rejects made-up codes (XX, ZZ) and guarantees the
    accepted code resolves a timezone later.
    """
    import pytz

    code = value.strip()
    if len(code) == 2 and code.upper() in pytz.country_timezones:
        return True
    return "Enter a valid ISO 3166 alpha-2 country code (e.g. US, GB, DE)."


def _looks_like_email(value: str) -> bool | str:
    value = value.strip()
    # Minimal shape check — a single @ with non-empty local part and a dotted domain.
    local, _, domain = value.partition("@")
    if local and "." in domain and not domain.startswith(".") and not domain.endswith("."):
        return True
    return "Enter a valid email address (e.g. you@example.com)."


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


def _finalize_account(operator_email: str, country: str, newsletter: bool) -> None:
    """Persist country, create the operator ``User`` from their own email, subscribe once.

    ``operator_email`` is the human's inbox (BCC + newsletter target), distinct
    from the mailbox ``from_address`` used as the sending identity.
    """
    from openoutreach.core.models import Campaign, SiteConfig
    from openoutreach.emails.models import Mailbox
    from openoutreach.emails.newsletter import subscribe_to_newsletter

    box = Mailbox.objects.first()
    if box is None:  # the mailbox step runs before this one; defensive
        raise OnboardingCancelled

    cfg = SiteConfig.load()
    cfg.country_code = country
    cfg.save(update_fields=["country_code"])

    user = _create_operator(Campaign.objects.first(), operator_email)
    if newsletter:
        subscribe_to_newsletter(operator_email)
    logger.info("Operator account '%s' created (email=%s).", user.username, operator_email)


def _create_operator(campaign, email: str):
    """Create the operator Django ``User`` from their email (the human's own inbox)."""
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
    if all(step.is_done() for step in STEPS):
        return  # nothing to do — don't print the intro on a fully-onboarded run

    from openoutreach.core.logging import print_banner

    print_banner()
    print(_INTRO)
    for step in STEPS:
        if not step.is_done():
            step.run()
