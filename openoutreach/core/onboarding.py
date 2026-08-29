# openoutreach/core/onboarding.py
"""Onboarding as an ordered list of idempotent steps.

First principles
----------------
Onboarding is a **sequence of independent steps**. Each step knows two things:

  * ``is_done()`` — is this already satisfied? (reads the DB, never prompts)
  * ``run()``     — collect what's missing and **persist it immediately**

The runner executes only the steps whose ``is_done()`` is false, in order. Because
every step persists the moment it succeeds, a crash or Ctrl+C mid-onboarding
resumes exactly where it stopped, and a satisfied step is never revisited.

Why this shape kills the "onboarding keeps looping back" bug:

  * The **only** thing that decides ordering is ``is_done()``. Once a step's state
    is persisted it is done — the runner cannot land back on it.
  * A step's ``run()`` owns its **own** retry loop. A credential that fails
    verification re-asks *that step's* fields (with what you typed retained) —
    it never rewinds to an earlier step, and never restarts the whole wizard.
  * There is no end-of-wizard ``apply()`` that could half-fail and strand state:
    each step is its own commit point.

Cancellation is a single exception, not a return value threaded through every
caller: the wizard prompts return ``None`` on Ctrl+C, and ``_required()`` turns that
into ``OnboardingCancelled`` at one boundary.

Order: campaign → LLM (live-verified) → **BetterContact key** → account (your email
+ country + newsletter + legal, then the operator ``User``). The BetterContact key
is mandatory because it powers both discovery and enrichment — note the barrier is
an *account*, not a bill: the Lead Finder search is free and only the address lookup
costs a credit.

**Four steps, down from six.** The mailbox (seven fields, SMTP auth-checked) and the
signature came out with the sending leg. That is most of the install path, and the
reason it mattered is not the typing — it is that a lead finder was asking someone to
connect an inbox, and accept a sending liability, before it had shown them a lead.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Callable, TypeVar

from openoutreach.core import onboarding_wizard as wiz

logger = logging.getLogger(__name__)

DEFAULT_CAMPAIGN_NAME = "Email Outreach"

_INTRO = """
  Welcome to OpenOutreach — a self-hosted lead finder that qualifies for you.
  Describe your product and who you sell to; it discovers matching people, judges
  each one against your ICP, and writes down why it chose them.

  OpenOutreach does not send email. The result prints as CSV with the column
  names your cold-email tool already reads (Instantly, Smartlead, or any CRM/
  spreadsheet) — import it into whatever you already send with, no column
  mapping needed.

  Setup takes a few minutes. Have two things ready:
    • an LLM provider key — the agent qualifies your leads and writes the reasons
    • a BetterContact key — powers lead discovery (free) and email finding (paid)

  OpenOutreach is free; you pay only the providers above. Stop anytime — setup
  resumes where you left off.
"""

# The canonical Legal Notice — the single source of truth for how OpenOutreach
# behaves toward the operator's mailbox and the people it contacts. The account
# step points at it by URL rather than rendering it, so onboarding stays a
# terminal prompt and not a page of reflowed Markdown.
LEGAL_NOTICE_URL = "https://github.com/eracle/OpenOutreach/blob/main/LEGAL_NOTICE.md"

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
# The environment — the second way in, and the only one an agent has
# ---------------------------------------------------------------------------
#
# An agent-driven install has no TTY, so this is the main path, not a fallback.
# Every field the wizard asks for is settable here; a step hydrates only when it
# has *all* of its own fields, because a half-filled step would persist state the
# wizard would then have to reconcile.

ENV_PREFIX = "OPENOUTREACH_"

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class OnboardingEnvError(SystemExit):
    """Raised when a variable is set but unusable — never silently ignored.

    A bad value is a different thing from an absent one: absent means *ask*, bad
    means *stop and say so*. Falling through to "missing" would print a variable
    the operator has already set.
    """

    def __init__(self, name: str, problem: str) -> None:
        super().__init__(f"error: bad_config: {ENV_PREFIX}{name}: {problem}")


def _env(name: str) -> str:
    """Read one onboarding variable, stripped. Absent and blank are the same thing."""
    return os.environ.get(ENV_PREFIX + name, "").strip()


def _env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean variable, rejecting anything that is not plainly yes or no."""
    raw = _env(name).lower()
    if not raw:
        return default
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise OnboardingEnvError(name, f"expected one of {sorted(_TRUE | _FALSE)}, got {raw!r}")


def _env_validated(name: str, value: str, validate: Callable[[str], bool | str]) -> str:
    """Apply a wizard validator to an environment value, raising on rejection."""
    verdict = validate(value)
    if verdict is not True:
        raise OnboardingEnvError(name, str(verdict))
    return value


# ---------------------------------------------------------------------------
# Step registry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Step:
    """One onboarding step: a name, a done-check, a runner, and an environment path.

    ``from_env`` is the third capability, and the reason a step stays the one place
    that knows its own fields: it reads the variables it owns, persists them if they
    are all there, and returns whether it did. ``env_keys`` is what an incomplete
    headless run prints — the variables that would have satisfied this step.
    """

    key: str
    is_done: Callable[[], bool]
    run: Callable[[], None]
    from_env: Callable[[], bool] = lambda: False
    env_keys: tuple[str, ...] = field(default=())


# ── Campaign: what you sell, and to whom ─────────────────────────

def _campaign_done() -> bool:
    from openoutreach.core.models import Campaign

    return Campaign.objects.exists()


def _run_campaign() -> None:
    from openoutreach.core.models import Campaign

    print(
        "\n  Campaign — describe what you sell and who you're selling to. This is the\n"
        "  whole input: it writes the opening search and trains the qualifier that\n"
        "  decides which leads fit. Name the traits that matter — industry, seniority,\n"
        "  company size — but don't over-narrow: a target specific enough to describe\n"
        "  one imagined person (an exact title at an exact kind of company doing an exact\n"
        "  thing) reads to the LLM as a checklist, and it will reject real leads for\n"
        "  missing some trait you never actually required. Describe the kind of\n"
        "  organization or role that fits, not a single ideal example of one."
    )
    Campaign.objects.create(
        name=DEFAULT_CAMPAIGN_NAME,
        product_docs=_required(wiz.multiline(
            "Product/service description — what it does, who it's for, the problem it solves "
            "(e.g. 'A self-hosted CI dashboard for small dev teams — replaces spreadsheet "
            "build-tracking; cuts flaky-test triage from hours to minutes')"
        )),
        campaign_target=_required(wiz.multiline(
            "Campaign target — who you're going after and the outcome you want. Broad enough "
            "to cover a real range of companies or roles, not one hyper-specific persona "
            "(e.g. 'book demos with engineering leaders at growth-stage SaaS companies', not "
            "'the VP of Platform Engineering at a 200-person Series B fintech using Kubernetes')"
        )),
    )
    logger.info("Campaign '%s' created.", DEFAULT_CAMPAIGN_NAME)


def _campaign_from_env() -> bool:
    from openoutreach.core.models import Campaign

    product = _env("PRODUCT_DESCRIPTION")
    target = _env("CAMPAIGN_TARGET")
    if not (product and target):
        return False

    Campaign.objects.create(
        name=_env("CAMPAIGN_NAME") or DEFAULT_CAMPAIGN_NAME,
        product_docs=product,
        campaign_target=target,
    )
    logger.info("Campaign created from the environment.")
    return True


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


def _llm_from_env() -> bool:
    """Hydrate the LLM step, verifying the credentials exactly as the wizard does.

    An unverified key is worse headless than interactively: there is nobody to
    re-ask, and the daemon would fail later inside a qualification instead of at
    boot, where the message is readable.
    """
    from openoutreach.core.llm import verify_llm_credentials

    model, key = _env("AI_MODEL"), _env("LLM_API_KEY")
    base = _env("LLM_API_BASE")
    if not (model and key):
        return False
    if model.startswith("openai_compatible:") and not base:
        raise OnboardingEnvError("LLM_API_BASE", "required for an openai_compatible:* model")

    error = verify_llm_credentials(model, key, base)
    if error is not None:
        raise OnboardingEnvError("LLM_API_KEY", error)
    _save_llm(model, key, base)
    return True


def _save_llm(model: str, key: str, base: str) -> None:
    from openoutreach.core.models import SiteConfig

    cfg = SiteConfig.load()
    cfg.ai_model, cfg.llm_api_key, cfg.llm_api_base = model, key, base
    cfg.save()
    logger.info("LLM config saved.")


# The mailbox and signature steps used to sit here, between the LLM and the finder
# key: connect a sending inbox (seven fields, SMTP auth-checked), then write the
# sign-off appended to every email it sends. Both are gone with the sending leg.
#
# They were also the most expensive thing on the install path, and the expense was
# not the typing. Behind the mailbox step stood `LEGAL_NOTICE.md` §4 — the disclosure
# that the tool would send the maintainer's promotional campaign from your mailbox,
# under your identity. Asking someone to accept a sending liability before they have
# seen a single lead was the wrong trade for a tool whose output is a CSV.


# ── BetterContact: powers discovery + enrichment (mandatory) ──────

def _bettercontact_done() -> bool:
    from openoutreach.enrichment import bettercontact

    return bettercontact.is_configured()


def _run_bettercontact() -> None:
    from openoutreach.core.models import SiteConfig
    from openoutreach.enrichment.bettercontact import SIGNUP_URL

    print(
        "\n  BetterContact — free account, 40 credits, no card.\n\n"
        "  Finding leads costs nothing. One credit buys one verified work\n"
        "  email, and only when you ask with --emails.\n\n"
        "  Get a key (affiliate link — supports OpenOutreach, no markup to you):\n"
        f"  {SIGNUP_URL}\n"
        "  Then paste the API key from your dashboard below."
    )
    cfg = SiteConfig.load()
    cfg.bettercontact_api_key = _required(wiz.text("BetterContact API key", secret=True))
    cfg.save()
    _say("  ✓ BetterContact key saved.", "fg:green")


def _bettercontact_from_env() -> bool:
    """Hydrate the finder keys from the environment.

    **The optional Apollo key is taken first, and independently of the outcome.** Apollo
    replaces only the resolver leg — discovery still needs the BetterContact key — so it
    is never enough on its own to call this step done, and returning early on a missing
    BetterContact key would silently drop a key the operator did set. The wizard has no
    Apollo prompt on purpose: a second, optional vendor does not belong in the path every
    first run walks.
    """
    from openoutreach.core.models import SiteConfig

    cfg = SiteConfig.load()
    changed = []

    apollo_key = _env("APOLLO_API_KEY")
    if apollo_key:
        cfg.apollo_api_key = apollo_key
        changed.append("Apollo key")

    preferred = _env("EMAIL_FINDER")
    if preferred:
        cfg.email_finder = preferred
        changed.append(f"finder preference ({preferred})")

    key = _env("BETTERCONTACT_API_KEY")
    if key:
        cfg.bettercontact_api_key = key
        changed.append("BetterContact key")

    if changed:
        cfg.save()
        logger.info("%s set from the environment.", ", ".join(changed).capitalize())

    return bool(key)


# ── Account: country + newsletter + information notice + legal, then the operator User ─

def _account_done() -> bool:
    """Done only when an operator exists *with a non-blank email* — the operator's
    own inbox (the contacts-store key and the newsletter target). Requiring a real
    email (not merely 'a staff user exists') stops a legacy blank-email account
    from short-circuiting the address prompt."""
    from django.contrib.auth.models import User

    return User.objects.filter(is_active=True, is_staff=True).exclude(email="").exists()


def _run_account() -> None:
    """Collect jurisdiction, gate on the Legal Notice, then create the operator.

    Nothing is persisted until every answer is in and the Legal Notice is
    accepted, so a declined/cancelled step leaves no partial state behind.
    """
    from openoutreach.core.geo import is_gdpr_protected

    # The operator's own inbox — the contacts-give-back key and (if opted in) the
    # newsletter target. It used to need saying that this was *not* the sending
    # mailbox; with no sending mailbox there is one address again.
    operator_email = _required(wiz.text(
        "Your email address. We'll send product updates here if you opt in below.",
        validate=_looks_like_email,
    )).strip()

    country = _required(wiz.text(
        "Your country (ISO 3166 alpha-2, e.g. US, GB, DE) — sets your email-jurisdiction "
        "defaults and filters leads to that country",
        validate=_looks_like_country,
    )).lower()

    # Newsletter opt-in defaults OFF in GDPR/opt-in jurisdictions (no consent by
    # silence), ON elsewhere. An explicit yes is lawful consent anywhere.
    newsletter = _required(wiz.confirm(
        "Subscribe to the OpenOutreach newsletter?",
        default=not is_gdpr_protected(country),
    ))
    _require_legal()
    _finalize_account(operator_email, country, newsletter)


def _account_from_env() -> bool:
    """Hydrate the account step, including the Legal Notice acceptance.

    **Acceptance is never inferred.** The variable has to say yes; absent leaves the
    step unsatisfied, so a headless run stops and names it rather than proceeding on
    a notice nobody agreed to. Newsletter defaults to off everywhere here — the
    wizard's jurisdiction-aware default is a *suggestion to a human*, and silence in
    a config file is not consent anywhere.
    """
    email, country = _env("OPERATOR_EMAIL"), _env("COUNTRY")
    if not (email and country and _env_bool("ACCEPT_LEGAL_NOTICE")):
        return False

    _env_validated("OPERATOR_EMAIL", email, _looks_like_email)
    _env_validated("COUNTRY", country, _looks_like_country)
    _finalize_account(email, country.lower(), _env_bool("NEWSLETTER"))
    return True


def _looks_like_country(value: str) -> bool | str:
    """Validate an ISO 3166-1 alpha-2 code.

    ``pytz.country_timezones`` is used only as a ready-made table of real country
    codes — nothing here reads a timezone from it — so a made-up code (XX, ZZ) is
    rejected without adding a second dependency just to validate two letters.
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
            f"Do you accept the Legal Notice? ({LEGAL_NOTICE_URL})",
            default=False,
        )
        if accepted is None:  # Ctrl+C
            raise OnboardingCancelled
        if accepted:
            return
        _say("  You must accept the Legal Notice to use OpenOutreach.", "fg:red")


def _finalize_account(operator_email: str, country: str, newsletter: bool) -> None:
    """Persist country, create the operator ``User`` from their own email, subscribe once.

    ``operator_email`` is the human's inbox — the contacts-store key and the
    newsletter target. There is one operator address and it is not a sending
    identity; this side does not send.

    **This must stay satisfiable without a mailbox.** Requiring one here would weld
    the finder back to the sender and make the account step unreachable on an
    install that only ever exports a CSV.
    """
    from openoutreach.contacts.service import register_operator
    from openoutreach.core.models import Campaign, SiteConfig
    from openoutreach.core.newsletter import subscribe_to_newsletter

    cfg = SiteConfig.load()
    cfg.country_code = country
    cfg.save(update_fields=["country_code"])

    user = _create_operator(Campaign.objects.first(), operator_email)
    if newsletter:
        subscribe_to_newsletter(operator_email)
    logger.info("Operator account '%s' created (email=%s).", user.username, operator_email)

    # Identity, not entitlement, and not consent: the hub token names this install so it
    # can hold a balance, be metered and be revoked. It is minted here because the email
    # is already in hand — no new question — and **regardless of jurisdiction**, since the
    # EEA/UK/CH rule governs contributing records, which is a different act. Best-effort:
    # a hub that is down or that still demands a record leaves the token unset, and the
    # first contribution mints it the old way.
    register_operator()


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
    Step(
        "campaign", _campaign_done, _run_campaign, _campaign_from_env,
        ("PRODUCT_DESCRIPTION", "CAMPAIGN_TARGET"),
    ),
    Step(
        "llm", _llm_done, _run_llm, _llm_from_env,
        ("AI_MODEL", "LLM_API_KEY"),
    ),
    Step(
        "bettercontact", _bettercontact_done, _run_bettercontact, _bettercontact_from_env,
        ("BETTERCONTACT_API_KEY",),
    ),
    Step(
        "account", _account_done, _run_account, _account_from_env,
        ("OPERATOR_EMAIL", "COUNTRY", "ACCEPT_LEGAL_NOTICE"),
    ),
]


def missing_keys() -> set[str]:
    """Return the keys of steps that still need attention (empty ⇒ fully onboarded)."""
    return {step.key for step in STEPS if not step.is_done()}


def hydrate_from_env() -> set[str]:
    """Satisfy every unsatisfied step the environment can, returning the keys filled.

    Runs in step order so a later step can rely on an earlier one's rows (the account
    step attaches the operator to the campaign). A step that cannot be filled is left
    alone; a step whose variables are set but *wrong* raises rather than being skipped.
    """
    filled = set()
    for step in STEPS:
        if not step.is_done() and step.from_env():
            filled.add(step.key)
    return filled


def missing_env_keys() -> dict[str, tuple[str, ...]]:
    """Map each still-unsatisfied step to the variables that would satisfy it."""
    return {step.key: step.env_keys for step in STEPS if not step.is_done()}


def env_help() -> str:
    """Render the unsatisfied steps as the variables to set — the headless exit text."""
    lines = [
        f"  {key}: {', '.join(ENV_PREFIX + name for name in names)}"
        for key, names in missing_env_keys().items()
    ]
    return "\n".join(lines)


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
