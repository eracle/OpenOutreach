# openoutreach/enrichment/provider.py
"""The email-finder seam: one interface, one configured provider, two transports.

``lookup.py`` drives *a* provider, never a named one. Which module answers is decided
here by ``active()``, from whichever key the operator configured — so an install with an
Apollo key and an install with a BetterContact key run the same pipeline, the same
states and the same hub give-back, and neither knows about the other's vendor.

**The interface spans sync and async, because the two real providers differ.**
BetterContact is a waterfall that takes seconds to minutes: ``start`` fires a job and
returns a handle, the deal parks at FINDING_EMAIL, and ``poll_once`` checks it later.
Apollo's ``people/match`` answers in the same HTTP call. Forcing either into the other's
shape would cost something real — a fake handle for Apollo would invent a poll that
resolves instantly and a state the deal passes through in microseconds, while blocking
on BetterContact would hold a run open for minutes per lead. So ``start`` returns a
``Lookup`` that is *either* a finished ``PollOutcome`` or a handle to poll, and the
caller branches once on which it got.

A provider module is a plain module — no classes to register, no plugin table. It
implements:

    NAME            str, the wire value for the hub's contribution origin
    SIGNUP_URL      str, the one attributed path to an account
    is_configured() bool — is there a key
    credit_balance() int — raises ProviderUnavailable if it cannot be read
    start(url)      Lookup — resolved outright, or a handle
    poll_once(id)   PollOutcome — async providers only; sync ones never see a call
"""
from __future__ import annotations

from dataclasses import dataclass

from openoutreach.core.errors import ErrorType


class ProviderUnavailable(Exception):
    """The finder could not run — no key, or the service was unreachable.

    Distinct from a genuine miss (it ran and found no address), which is a terminal
    answer about the lead rather than about the provider. Carries a stable
    ``error_type`` from ``core.errors.ErrorType`` so a caller can tell *why* without
    matching on the message.
    """

    def __init__(self, message: str, error_type: str = ErrorType.PROVIDER_UNAVAILABLE) -> None:
        self.error_type = error_type
        super().__init__(message)


@dataclass(frozen=True)
class PollOutcome:
    """A lookup's state at one moment: still running, or terminated hit/miss.

    A hit carries the name parts the provider resolved alongside the address. Both
    vendors return them in the same response that carries the email — same call, same
    credit — which is why nothing in this codebase splits a full name itself.
    """
    running: bool
    email: str = ""
    first_name: str | None = None
    last_name: str | None = None

    @property
    def hit(self) -> bool:
        return not self.running and bool(self.email)

    @property
    def miss(self) -> bool:
        return not self.running and not self.email


@dataclass(frozen=True)
class Lookup:
    """What ``start`` returned: a finished answer, or a handle to poll.

    Exactly one is set. ``outcome`` means the provider answered synchronously and the
    deal never touches FINDING_EMAIL; ``request_id`` means a job is in flight and the
    deal parks on the handle.
    """
    outcome: PollOutcome | None = None
    request_id: str = ""

    @property
    def pending(self) -> bool:
        return self.outcome is None


def active():
    """The provider module this install resolves with, or ``None`` if none is configured.

    Selection is by configured key, so the common case — one account, one key — needs no
    setting at all. ``email_finder`` breaks the tie only when both keys are present;
    unset, BetterContact wins it, because it is the incumbent and the affiliate path,
    and silently moving an existing install's spend to a second vendor on the strength
    of a stray key would be the wrong default.
    """
    from openoutreach.core.models import SiteConfig
    from openoutreach.enrichment import apollo, bettercontact

    by_name = {bettercontact.NAME: bettercontact, apollo.NAME: apollo}
    preferred = SiteConfig.load().email_finder

    if preferred:
        chosen = by_name.get(preferred)
        return chosen if chosen and chosen.is_configured() else None

    for module in (bettercontact, apollo):
        if module.is_configured():
            return module
    return None


def by_name(name: str):
    """The provider module called *name*, or ``None``.

    Used to poll a job with the vendor that issued its handle rather than with whichever
    vendor is configured *now* — an operator who swaps keys while a lookup is in flight
    would otherwise hand BetterContact's ``request_id`` to Apollo, which can only answer
    that it does not poll.
    """
    from openoutreach.enrichment import apollo, bettercontact

    return {bettercontact.NAME: bettercontact, apollo.NAME: apollo}.get(name)


def configured() -> list:
    """Every provider module with a key set — for ``status``, which reports on all of
    them rather than only the one that would run."""
    from openoutreach.enrichment import apollo, bettercontact

    return [m for m in (bettercontact, apollo) if m.is_configured()]
