"""One onboarding for both children, writing each one's own singleton through its model.

Two tools used to mean two wizards and two env files. There is one flow here, and it is
not a third config schema: the finder's `init` still owns the questions it always owned —
the validators, the live LLM check, the Legal Notice gate — and what this adds is only
what falls between the two installs.

**Three things fall between them**, and each is a real gap rather than tidiness:

  * **The five fields both singletons hold under the same names** — what you sell, who
    for, and the model that writes it. Asked once by the finder, copied onto the sender's
    row through the sender's model, which validates it. This is the reason to host both
    app sets in one registry rather than shell out: the alternative is a translation layer
    restating both config surfaces as env-var strings, and it drifts silently.
  * **The operator's name.** Both children read the same Django `User`. The finder creates
    it from the email address with no `first_name`; the sender's own `_ensure_operator`
    then sees a user and skips — so every message would be signed with an email handle.
  * **The booking link.** The sender offers it only alongside a missing *required* message
    field, and under this host those arrive already answered — so the one moment it is
    cheap to ask never comes unless it is asked here.

**Everything is asked on stderr, the caret included.** `input`'s own prompt argument
writes to stdout, which on this program carries the CSV.
"""
from __future__ import annotations

import os
import sys

#: The fields both `SiteConfig`s hold under the same names. Copied finder → sender, so
#: the operator answers them once.
SHARED_FIELDS = ("product_docs", "campaign_target", "ai_model", "llm_api_key", "llm_api_base")

_PROMPTS = {
    "name": "Who is sending this mail? The name that signs it.",
    "booking_link": "A link to book a call, if you have one (blank to skip).",
}


def onboard(*init_args: str) -> None:
    """Collect everything a find-then-send pass needs, in one flow.

    The order is the order the answers depend on each other in: the finder's `init`
    migrates the schema and creates the operator, so there is a row to copy onto and a
    user to name only once it has run. `init_args` are its own flags, passed through
    untouched — `--product-docs FILE` and `--target FILE` are pages of prose that a
    command line would corrupt quietly, and this host has no business restating them.
    """
    from django.core.management import call_command

    # `init`'s report is a result on its own command line; here it is narration, and
    # stdout belongs to the leads.
    call_command("init", *init_args, stdout=sys.stderr)
    _seed_sender_config()
    _name_the_operator()

    from cold_outreach import first_run

    first_run.ensure_ready()


def _seed_sender_config() -> None:
    """Fill the sender's singleton from the environment, then from the finder's answers.

    The environment goes first because a variable the operator set on the sender's own
    names is a deliberate override; the finder's answer is the default for a field nobody
    has spoken to twice. Neither overwrites a filled field — that is the rule both
    children already follow, and a stale variable reverting an edited row is what it
    exists to prevent.
    """
    from cold_outreach.core.models import SiteConfig as SenderConfig
    from cold_outreach.core.models import hydrate_message_from_environment, hydrate_llm_from_environment
    from openoutfind.core.models import SiteConfig as FinderConfig

    sender = SenderConfig.load()
    hydrate_message_from_environment(sender)
    hydrate_llm_from_environment(sender)

    finder = FinderConfig.load()
    for field in SHARED_FIELDS:
        answer = getattr(finder, field)
        if answer and not getattr(sender, field):
            setattr(sender, field, answer)

    if not sender.booking_link and sys.stdin.isatty():
        sender.booking_link = _ask(_PROMPTS["booking_link"])
    sender.save()


def _name_the_operator() -> None:
    """Give the one operator a name to sign with, or stop naming the variable that would.

    The finder makes the `User` out of the email address, so `first_name` is empty and
    the sender — which skips a user that already exists — would never ask.
    """
    from cold_outreach.core.operator import get_active_user, set_operator
    from cold_outreach.errors import OutsendError
    from cold_outreach.first_run import OPERATOR_ENV

    operator = get_active_user()
    if operator is None or operator.first_name:
        return

    name = (os.environ.get(OPERATOR_ENV["name"]) or "").strip()
    if not name and sys.stdin.isatty():
        name = _ask(_PROMPTS["name"])
    if not name:
        raise OutsendError(f"nobody to sign the mail — set {OPERATOR_ENV['name']}")

    set_operator(full_name=name, email=operator.email)


def _ask(question: str) -> str:
    """Ask on stderr and read one line back."""
    print(f"\n{question}", file=sys.stderr)
    print("> ", end="", file=sys.stderr, flush=True)
    return input().strip()
