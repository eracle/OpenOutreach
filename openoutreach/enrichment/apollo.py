# openoutreach/enrichment/apollo.py
"""Apollo email lookup — resolve a work email for a qualified lead, synchronously.

**Wired but not exposed, and never run against a live key.** Nothing documents
``apollo_api_key`` and no wizard step offers it, so this module only runs for someone who
sets the key deliberately. Two facts are still unverified because they need an account:
which plan tier unlocks ``people/match``, and whether ``credit_balance`` works at all —
it needs a master key, which may be Organization-tier only. Everything below is written
from the published API docs and tested against mocks. Before exposing it: verify both on
a real key, add the ``/go/apollo`` redirect (see ``SIGNUP_URL``), teach the hub an
``apollo`` contribution origin, and validate ``SiteConfig.email_finder`` so a typo raises
instead of silently disabling enrichment.

Apollo's ``people/match`` answers in one HTTP call, so ``start`` returns a finished
``PollOutcome`` and ``poll_once`` is never reached: there is no job, no handle, and the
deal goes READY_TO_FIND_EMAIL → RESOLVED without passing through FINDING_EMAIL. That is
the whole difference from BetterContact; everything above this module is shared.

**Only the work email is asked for.** ``reveal_personal_emails`` and
``reveal_phone_number`` stay off: the personal address is a separate credit and a
GDPR-gated field this product has no use for, the mobile is eight credits, and either
flag switches the endpoint to asynchronous webhook delivery — which would drag the whole
async handshake back in for data we do not want. ``run_waterfall_email`` is off for the
same reason plus a worse one: it can bill per vendor lookup *regardless of result*,
turning a miss from free into expensive.

**A miss costs nothing.** Apollo charges 1 credit for an address and 0 when it finds
none, so an unresolvable lead is free — unlike a submit-and-poll vendor, where the
credit goes the moment the job is accepted.
"""
from __future__ import annotations

import logging

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from openoutreach.core.errors import ErrorType
from openoutreach.enrichment.provider import Lookup, PollOutcome, ProviderUnavailable

logger = logging.getLogger(__name__)

NAME = "apollo"

SIGNUP_URL = "https://openoutreach.app/go/apollo"
"""The one path to an Apollo account, carrying the project's attribution.

Same contract as BetterContact's: attribution is won at **signup**, never repaired
afterwards, so no caller may write a bare apollo.io URL. Path-only because terminals
stop linkifying at a ``?``; the redirect applies the partner parameter.

**The redirect does not exist yet** — ``openoutreach.app/netlify.toml`` has no
``/go/apollo`` rule, so this 404s today. It is reachable only from ``status``'s
top-up prompt and only on an install that set an Apollo key, which is nobody while the
provider stays unexposed. Add the rule with the PartnerStack parameter before exposing.
"""

_MATCH_URL = "https://api.apollo.io/api/v1/people/match"
_USAGE_URL = "https://api.apollo.io/api/v1/usage_stats/api_usage_stats"
_HTTP_TIMEOUT_S = 30

# Same 429 policy as the other finder: back off, never retry at speed. Apollo publishes
# per-minute, per-hour and per-day windows and answers a breach with a 429, so a client
# that hammers through one burns the operator's daily allowance on nothing.
_RETRY = Retry(
    total=5,
    status_forcelist=(429,),
    allowed_methods=frozenset({"GET", "POST"}),
    backoff_factor=5,
    backoff_max=120,
    respect_retry_after_header=True,
    raise_on_status=True,
)

# Apollo grades every address it returns. Only a verified one is worth a send; a
# "guessed" address is a pattern-matched invention (first.last@domain) with no delivery
# evidence behind it, and treating it as a hit would put bounces in the operator's
# export under the same column as real addresses.
_USABLE_STATUSES = frozenset({"verified"})

# What Apollo returns in the ``email`` field when the account cannot see the address:
# a syntactically valid placeholder rather than a null. Exported unchecked it would look
# exactly like a resolved lead, so it is matched explicitly and read as a miss.
_LOCKED_PLACEHOLDER = "email_not_unlocked@domain.com"


class ApolloUnavailable(ProviderUnavailable):
    """Apollo could not run — no key, wrong plan tier, or the service was unreachable."""


def is_configured() -> bool:
    """True when an Apollo API key is set."""
    from openoutreach.core.models import SiteConfig

    return bool(SiteConfig.load().apollo_api_key)


def start(profile_url: str) -> Lookup:
    """Resolve *profile_url* to a work email in one call.

    Returns a terminated ``Lookup`` either way — Apollo has no in-flight state to hand
    back. Only the profile URL is sent, matching the deliberate minimum the other
    provider is held to: the less of a lead's record leaves for a third party, the
    better.
    """
    body = _post(_MATCH_URL, {"linkedin_url": profile_url})
    return Lookup(outcome=_to_outcome(body.get("person") or {}))


def poll_once(request_id: str) -> PollOutcome:
    """Never called — ``start`` always terminates. Present so the module satisfies the
    provider interface, and loud rather than silent if the caller ever branches wrong."""
    raise ApolloUnavailable(
        "Apollo resolves synchronously and issues no job handle; there is nothing to poll")


def credit_balance() -> int:
    """Credits left, read from the usage-stats endpoint (which itself costs nothing).

    **This needs a master key**, or one explicitly scoped to the usage-stats path;
    Apollo answers anything else with a 403, which surfaces as an auth error rather
    than a zero balance — a balance we could not read is not a balance of zero.
    """
    body = _post(_USAGE_URL, {})
    return _as_credit_count(body)


# ── Response reading ───────────────────────────────────────────────


def _to_outcome(person: dict) -> PollOutcome:
    """One matched person → a terminal hit or miss.

    A miss is the common, cheap case: no match, a locked placeholder, or an address
    Apollo will not vouch for. All three are the same answer to the funnel — this
    person has no usable address — and all three cost 0 credits.
    """
    email = (person.get("email") or "").strip()
    status = (person.get("email_status") or "").lower()

    if not email or email == _LOCKED_PLACEHOLDER or status not in _USABLE_STATUSES:
        return PollOutcome(running=False)

    return PollOutcome(
        running=False,
        email=email,
        first_name=person.get("first_name"),
        last_name=person.get("last_name"),
    )


def _as_credit_count(body: dict) -> int:
    """Credits remaining, from whichever shape the usage payload carries them in.

    Apollo reports per-endpoint windows (minute/hour/day) rather than one wallet, so the
    day's ``left_over`` for the match endpoint is the number that actually predicts
    whether the next lead can be resolved. An unreadable payload raises rather than
    reporting zero.
    """
    for key, windows in body.items():
        if "people/match" in str(key) and isinstance(windows, dict):
            day = windows.get("day") or {}
            left = day.get("left_over")
            if isinstance(left, (int, float)):
                return int(left)

    raise ApolloUnavailable(f"Apollo returned no readable usage for people/match: {body!r}")


# ── Transport ──────────────────────────────────────────────────────


def _require_key() -> str:
    from openoutreach.core.models import SiteConfig

    api_key = SiteConfig.load().apollo_api_key
    if not api_key:
        raise ApolloUnavailable("No Apollo API key configured", ErrorType.NO_CREDENTIAL)
    return api_key


def _post(url: str, body: dict) -> dict:
    """POST *body* to *url* and return the decoded response.

    Every failure becomes ``ApolloUnavailable`` with the error type that tells a reader
    what to do about it: a 401/403 is the operator's key or plan tier and needs a human,
    a 402 is an empty wallet, a network drop is transient and worth retrying.
    """
    api_key = _require_key()
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=_RETRY))
    session.headers.update({
        "x-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    })

    try:
        response = session.post(url, json=body, timeout=_HTTP_TIMEOUT_S)
        _raise_for_status(response)
        return response.json()
    except (requests.RequestException, TimeoutError) as exc:
        raise ApolloUnavailable(f"Apollo unreachable: {exc}") from exc
    finally:
        session.close()


def _raise_for_status(response: requests.Response) -> None:
    """Turn an error status into ``ApolloUnavailable``, narrowed by what it means."""
    if response.ok:
        return

    narrowed = {
        401: (ErrorType.PROVIDER_AUTH, "Apollo rejected the API key"),
        403: (ErrorType.PROVIDER_AUTH,
              "Apollo refused the call — the key lacks scope for this endpoint, "
              "or the plan tier does not include it"),
        402: (ErrorType.PROVIDER_OUT_OF_CREDITS, "Apollo reports no credits left"),
        429: (ErrorType.PROVIDER_RATE_LIMITED, "Apollo rate limit reached"),
    }.get(response.status_code)

    if narrowed:
        error_type, message = narrowed
        raise ApolloUnavailable(message, error_type)

    raise ApolloUnavailable(f"Apollo returned HTTP {response.status_code}")
