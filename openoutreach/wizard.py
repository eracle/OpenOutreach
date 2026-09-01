"""One onboarding, one row, and the environment both children are then handed.

Two tools used to mean two wizards and two env files; then it meant one flow writing each
child's own `SiteConfig` through that child's model. Neither child has a config model any
more — they read `OPENOUTFIND_*` / `OUTSEND_*` fresh on every run and remember nothing —
so what is left is the simplest shape yet: **ask a person once, keep the answers here,
export them into the environment before either child runs.**

**This dissolves the old objection rather than ignoring it.** The case against shelling out
to the children was that it needed "a translation layer restating both config surfaces as
env-var strings, and it drifts silently". The drift was possible because each child had a
*second* surface — a model with its own fields and validators — for the strings to drift
against. With the environment as a child's only surface, exporting it is not a translation
of anything: it is writing the one interface the child has, and a child whose variable
stops arriving says so by name on its next run.

**The wizard asks only what is missing, and never twice.** Every question is skipped when
the row already answers it or the environment already carries it, so a re-run is a no-op
and an operator who exported their own variables is not asked to repeat them into a
prompt.

**Everything is asked on stderr, the caret included.** `input`'s own prompt argument writes
to stdout, which on this program carries the CSV.
"""
from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

from openoutreach.config.models import FINDER_ENV, SENDER_ENV, SiteConfig

LEGAL_NOTICE_URL = "https://github.com/eracle/OpenOutreach/blob/main/LEGAL_NOTICE.md"

BETTERCONTACT_SIGNUP_URL = "https://bettercontact.rocks?fpr=openoutreach"

_INTRO = """
  Welcome to OpenOutreach — it finds B2B leads that fit what you sell, writes down why
  each one fits, and emails the ones you want it to.

  Setup takes a few minutes and is asked once. Have three things ready:
    • an LLM provider key — the agent judges your leads and writes the mail
    • a BetterContact key — powers lead discovery (free) and email finding (paid)
    • a mailbox and its app password — the address the mail comes from

  You pay only those providers. Stop anytime; setup resumes where you left off.
"""


def onboard(product_docs: str | None = None, target: str | None = None) -> None:
    """Fill in whatever this install has not been told, then hand it to both children.

    The order is what the answers depend on: the schema has to exist before a row can be
    written, the row has to be written before it can be exported, and both children check
    what they were given only once the environment carries it.

    ``product_docs`` / ``target`` are file paths — a product description is a page of
    markdown, and shell-quoting one is a way to corrupt it quietly.
    """
    from django.core.management import call_command

    # Django's migration narration is prose; stdout belongs to the leads.
    call_command("migrate", "--no-input", stdout=sys.stderr)

    config = SiteConfig.load()
    _read_files(config, product_docs, target)
    _ask_what_is_missing(config)
    config.save()

    apply_to_environment(config)
    _check_children()


def apply_to_environment(config: SiteConfig) -> None:
    """Put this row where the children look, without overruling an explicit export.

    `setdefault`, deliberately: a variable already in the environment was set by an
    operator or a unit file for this one run, and a stored answer quietly reverting it is
    the failure the children's own "environment seeds, never reverts" rule prevented.
    """
    for variable, value in config.export().items():
        os.environ.setdefault(variable, value)


# ── the questions ────────────────────────────────────────────────

def _ask_what_is_missing(config: SiteConfig) -> None:
    """Ask for every answer this install still lacks, or stop naming the variables.

    Headless, there is nobody to ask — so a missing answer is reported the way the
    children report one, as the variable that would supply it. That keeps one vocabulary
    across all three programs: whatever a person is asked here, an agent sets there.
    """
    missing = [field for field in _REQUIRED if not _answered(config, field)]
    # The notice is a record of something a person agreed to, so an environment that
    # already says yes is that person having said it somewhere else — the same reading
    # the children give it.
    accepted = config.accepted_legal_notice or _accepted_in_environment()
    if not missing and accepted:
        return

    if not sys.stdin.isatty():
        unanswered = list(missing) if accepted else [*missing, "accepted_legal_notice"]
        raise SystemExit(
            "error: onboarding_incomplete: nobody to ask, and this install has not been "
            "told: " + ", ".join(_variables_for(unanswered)))

    _say(_INTRO)
    for field in missing:
        _ASK[field](config)
    if not accepted:
        config.accepted_legal_notice = _accept_the_legal_notice()
        config.newsletter = _confirm("Subscribe to the OpenOutreach newsletter?")


def _accepted_in_environment() -> bool:
    return os.environ.get(FINDER_ENV["accepted_legal_notice"], "").strip().lower() in {
        "1", "true", "yes", "on"}


def _ask_campaign(config: SiteConfig) -> None:
    _say(
        "\n  What you sell, and to whom. This is the whole input: it writes the opening\n"
        "  search, trains the qualifier that decides which leads fit, and is what every\n"
        "  message is written from. Describe the kind of organization or role that fits —\n"
        "  a target narrow enough to describe one imagined person reads to the model as a\n"
        "  checklist, and it will turn down real leads for missing a trait you never meant\n"
        "  to require."
    )
    config.product_docs = _ask_paragraph(
        "Your product or service — what it does, who it is for, the problem it solves")
    config.campaign_target = _ask_paragraph(
        "Who you are going after, and the outcome you want")


def _ask_llm(config: SiteConfig) -> None:
    """Ask for a model and a key, and prove they work before writing them down.

    Verified here rather than only at run time because there is somebody standing at the
    prompt: a rejected key can be re-typed now, where an hour later it is a run that
    stopped.
    """
    from openoutfind.core.llm import verify_llm_credentials

    _say("\n  The model that judges leads and writes your mail.")
    while True:
        config.ai_model = _ask(
            "Model, as provider:model (e.g. anthropic:claude-sonnet-4-5-20250929, "
            "openai:gpt-4o, groq:llama-3.3-70b)")
        config.llm_api_key = _ask_secret("API key for that provider")
        if config.ai_model.startswith("openai_compatible:"):
            config.llm_api_base = _ask(
                "API base URL (OpenRouter / Together / Ollama / vLLM)")

        _say("  Verifying…")
        refused = verify_llm_credentials(
            config.ai_model, config.llm_api_key, config.llm_api_base)
        if refused is None:
            _say("  ✓ the model answered.")
            return
        _say(f"  ✗ {refused}")


def _ask_bettercontact(config: SiteConfig) -> None:
    _say(
        "\n  BetterContact — free account, 40 credits, no card.\n\n"
        "  Finding leads costs nothing. One credit buys one verified work email, and\n"
        "  only when you ask for addresses.\n\n"
        "  Get a key (affiliate link — supports the project, no markup to you):\n"
        f"  {BETTERCONTACT_SIGNUP_URL}"
    )
    config.bettercontact_api_key = _ask_secret("BetterContact API key")


def _ask_operator(config: SiteConfig) -> None:
    _say("\n  Who is running this, and who signs the mail.")
    config.operator_name = _ask("Your name, as it should sign your mail")
    config.operator_email = _ask("Your email address", validate=_looks_like_email)
    config.country_code = _ask(
        "Your country (ISO 3166 alpha-2, e.g. US, GB, DE) — your own jurisdiction, "
        "which decides your email rules", validate=_looks_like_country).lower()


def _ask_mailbox(config: SiteConfig) -> None:
    """Ask for the box the mail leaves from. The password is an app password.

    Nothing is checked here: connecting a box is an SMTP login, the sender does it when it
    first stores the box, and a second login from this side would be the same check in two
    places disagreeing about which one is authoritative.
    """
    _say(
        "\n  The mailbox your outreach is sent from. Use an **app password**, not your\n"
        "  login password — Google and most providers reject the latter for SMTP.\n"
        "  A non-Google provider also needs its four host/port values; leave them blank\n"
        "  for Google Workspace."
    )
    config.mailbox_address = _ask("Mailbox address", validate=_looks_like_email)
    config.mailbox_password = _ask_secret("App password for that mailbox")
    config.smtp_host = _ask("SMTP host (blank for Google)", required=False)
    config.smtp_port = _ask("SMTP port (blank for Google)", required=False)
    config.imap_host = _ask("IMAP host (blank for Google)", required=False)
    config.imap_port = _ask("IMAP port (blank for Google)", required=False)
    config.signature = _ask_paragraph(
        "The sign-off appended to every message (blank for none)", required=False)
    config.booking_link = _ask(
        "A link to book a call, if you have one (blank to skip)", required=False)


#: What an install cannot run without, in the order it is asked for. Each key names the
#: block of questions that fills it, so a partially-answered install resumes at the block
#: it stopped in rather than at the beginning.
_ASK = {
    "product_docs": _ask_campaign,
    "ai_model": _ask_llm,
    "bettercontact_api_key": _ask_bettercontact,
    "operator_email": _ask_operator,
    "mailbox_address": _ask_mailbox,
}
_REQUIRED = tuple(_ASK)


def _answered(config: SiteConfig, field: str) -> bool:
    """Whether this install already knows a field — from the row, or from the environment.

    The environment counts, because a variable an operator exported is an answer they
    have already given; asking for it again at a prompt would be this program failing to
    notice it was told.
    """
    if getattr(config, field):
        return True
    variable = FINDER_ENV.get(field) or SENDER_ENV.get(field)
    return bool(variable and os.environ.get(variable, "").strip())


def _variables_for(fields) -> list[str]:
    """The variables a headless install would set instead of answering these questions."""
    names = []
    for field in fields:
        variable = FINDER_ENV.get(field) or SENDER_ENV.get(field)
        if variable:
            names.append(variable)
    return names


def _accept_the_legal_notice() -> bool:
    """Gate onboarding on the Legal Notice; re-ask a decline rather than proceeding."""
    while True:
        if _confirm(f"Do you accept the Legal Notice? ({LEGAL_NOTICE_URL})"):
            return True
        _say("  You must accept the Legal Notice to use OpenOutreach.")


# ── handing it over ──────────────────────────────────────────────

def _check_children() -> None:
    """Let each child say whether what it was handed is enough for it.

    Each check is the child's own, unchanged and unwrapped: the finder pings the model and
    writes the operator row, the sender connects the mailbox by an SMTP login and records
    who signs. Neither is re-implemented here, so there is one place that knows what a
    find needs and one that knows what a send needs.
    """
    from cold_outreach import first_run as sender_first_run
    from openoutfind.core import readiness as finder_readiness

    finder_readiness.check_ready()
    sender_first_run.check_ready()


# ── prompt primitives ────────────────────────────────────────────

def _read_files(config: SiteConfig, product_docs: str | None, target: str | None) -> None:
    """Take the two long fields from files when they were passed that way."""
    for path, field in ((product_docs, "product_docs"), (target, "campaign_target")):
        if not path:
            continue
        text = Path(path).expanduser().read_text(encoding="utf-8").strip()
        if not text:
            raise SystemExit(f"error: bad_config: {path} is empty")
        setattr(config, field, text)


def _say(message: str) -> None:
    print(message, file=sys.stderr)


def _ask(question: str, *, required: bool = True, validate=None) -> str:
    """Ask on stderr and read one line back, re-asking until the answer is usable."""
    while True:
        _say(f"\n{question}")
        print("> ", end="", file=sys.stderr, flush=True)
        answer = input().strip()
        if not answer:
            if not required:
                return ""
            _say("  This one is needed.")
            continue
        verdict = validate(answer) if validate else True
        if verdict is True:
            return answer
        _say(f"  {verdict}")


def _ask_secret(question: str) -> str:
    """Ask for a credential without echoing it. Prompt on stderr like every other."""
    while True:
        _say(f"\n{question}")
        answer = getpass.getpass("> ", stream=sys.stderr).strip()
        if answer:
            return answer
        _say("  This one is needed.")


def _ask_paragraph(question: str, *, required: bool = True) -> str:
    """Ask for prose: read lines until a blank one, so a paragraph survives intact."""
    while True:
        _say(f"\n{question}")
        _say("  (several lines are fine — finish with an empty line)")
        lines = []
        while True:
            print("> ", end="", file=sys.stderr, flush=True)
            line = input()
            if not line.strip():
                break
            lines.append(line)
        answer = "\n".join(lines).strip()
        if answer or not required:
            return answer
        _say("  This one is needed.")


def _confirm(question: str) -> bool:
    """A yes/no question, defaulting to **no** — silence is never taken for consent."""
    return _ask(f"{question} [y/N]", required=False).lower().startswith("y")


def _looks_like_email(value: str) -> bool | str:
    local, _, domain = value.partition("@")
    if local and "." in domain and not domain.startswith(".") and not domain.endswith("."):
        return True
    return "Enter a valid email address (e.g. you@example.com)."


def _looks_like_country(value: str) -> bool | str:
    """Validate an ISO 3166-1 alpha-2 code against a real list of them.

    `pytz.country_timezones` is used only as a ready-made table of country codes —
    nothing here reads a timezone from it — so a made-up code is rejected without a second
    dependency for the sake of two letters.
    """
    import pytz

    if len(value) == 2 and value.upper() in pytz.country_timezones:
        return True
    return "Enter a valid ISO 3166 alpha-2 country code (e.g. US, GB, DE)."
