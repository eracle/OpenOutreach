# openoutreach/enrichment/bettercontact.py
"""BetterContact email lookup — resolve a work email for a qualified lead.

The paid finder is a **two-leg async handshake**, so the daemon never blocks on
a poll: ``submit(query)`` fires one job and returns its ``request_id``, and
``poll_once(request_id)`` checks that job exactly once (no wait), reporting
``running`` / ``hit`` / ``miss``. The collect task owns the retry backoff between
polls (its payload carries the ``request_id`` + deadline). ``is_configured()``
reports whether an API key is set. A missing key or an unreachable service
raises ``BetterContactUnavailable`` (never a bare error), so enrichment can't
take down the daemon. This is the *paid* finder — distinct from the free hub
lookup (``contacts.resolve``), tried first.

The blocking ``submit_and_poll`` transport remains for Lead Finder *discovery*
(``discovery.py``), which legitimately waits inside its own handler.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from openoutreach.core.errors import ErrorType
from openoutreach.core.logblock import step_line
from openoutreach.enrichment.provider import Lookup, PollOutcome, ProviderUnavailable

logger = logging.getLogger(__name__)

NAME = "bettercontact"

SIGNUP_URL = "https://openoutreach.app/go/email-finder"
"""The one path to an account, and it lives here so no caller can write it without the
affiliate parameter.

Attribution is won at **signup**, not at payment: an operator who creates the account
unattributed and later spends thousands earns the project nothing, and there is no way to
repair it afterwards. Every place the product offers an account — onboarding, ``status``,
an error message — resolves to this constant.

**It carries no query string, deliberately.** The direct link is
``https://bettercontact.rocks?fpr=openoutreach``, and plenty of terminals stop linkifying
at the ``?`` — so a printed URL is one careless click away from an unattributed signup
that nothing downstream can fix. A path-only URL survives that. The redirect
(``openoutreach.app/netlify.toml``, 302) is where the parameter is actually applied, which
also makes the destination swappable in one line without shipping a release.
"""

_ENRICH_URL = "https://app.bettercontact.rocks/api/v2/async"
_ACCOUNT_URL = "https://app.bettercontact.rocks/api/v2/account"
_POLL_INTERVAL_S = 5
_POLL_TIMEOUT_S = 300
_HTTP_TIMEOUT_S = 30

# **A 429 is backed off, never retried at speed** — their docs warn that a client which
# keeps firing through one can get the *account* blocked, which costs the operator far
# more than the throttled call was worth. urllib3 already does this correctly: waits of
# 5s, 10s, 20s, 40s (capped at ``backoff_max``), and ``Retry-After`` overrides the
# schedule whenever the response carries one. Only 429 is retried — a 401 or a 402 is a
# final answer, and repeating it would just be noise.
_RATE_LIMIT_ATTEMPTS = 5
_RETRY = Retry(
    total=_RATE_LIMIT_ATTEMPTS,
    status_forcelist=(429,),
    allowed_methods=frozenset({"GET", "POST"}),
    backoff_factor=5,
    backoff_max=120,
    respect_retry_after_header=True,
    raise_on_status=True,
)
_USABLE_STATUSES = frozenset({"valid", "deliverable", "catch_all_safe"})

# Cloudflare 403s a non-browser User-Agent (error 1010), so spoof a browser.
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class BetterContactUnavailable(ProviderUnavailable):
    """BetterContact could not run — no API key configured, or the service was
    unreachable. Distinct from a genuine miss (it ran, found no email).

    Carries a stable ``error_type`` from ``core.errors.ErrorType`` so a caller can
    tell *why* without matching on the message. The default is
    ``provider_unavailable``; the HTTP layer narrows it to auth, out-of-credits or
    rate-limited, which are three different things to a reader and to the funnel.

    Kept as its own name so existing callers and tests still catch it, but it is a
    ``ProviderUnavailable`` — ``lookup.py`` catches the base and never names a vendor.
    """


@dataclass(frozen=True)
class BetterContactQuery:
    """A lead to resolve. linkedin_url alone works; name/company lift the hit rate."""

    linkedin_url: str
    first_name: str = ""
    last_name: str = ""
    company: str = ""
    company_domain: str = ""


@dataclass(frozen=True)
class BetterContactResult:
    """One terminated lookup: the address, and the identity the provider resolved.

    The waterfall derives the contact from the URL internally and echoes back who it
    decided the person is. Those fields cost nothing extra — same call, same credit —
    and they are the *provider's* first/last name rather than a split of ours, which is
    why nothing in this codebase guesses at name parts. ``None`` where unreported.
    """
    email: str
    status: str
    first_name: str | None = None
    last_name: str | None = None


def start(profile_url: str) -> Lookup:
    """Fire one lookup job and hand back its handle — the async half of the interface.

    Never terminates here: BetterContact's waterfall walks 20+ vendors and takes
    seconds to minutes, so the deal parks at FINDING_EMAIL on the ``request_id`` and
    ``poll_once`` finishes the job later.

    **Only the profile URL is sent.** ``BetterContactQuery`` accepts name and company
    too and resolves better with them, but the lookup is deliberately minimal — the
    less of a lead's record leaves for a third party, the better, and URL-only is
    measured at ~42% usable (2026-06-11, 45 real leads), which is enough. Do not widen
    this query without a decision to widen it.
    """
    return Lookup(request_id=submit(BetterContactQuery(linkedin_url=profile_url)))


def is_configured() -> bool:
    """True when the BetterContact paid finder is configured (an API key is set)."""
    from openoutreach.core.models import SiteConfig

    return bool(SiteConfig.load().bettercontact_api_key)


def credit_balance() -> int:
    """Credits left on the operator's BetterContact account.

    The balance is **readable**, which is what lets the run warn before a lead fails
    rather than after a 402: ``GET /account`` → ``credits_left``, counted across the
    whole organisation. Raises ``BetterContactUnavailable`` with a 401 spelled out,
    since an invalid key and an unreachable service need different answers from a
    reader.
    """
    api_key = _require_key()
    with _session(api_key) as session:
        try:
            body = _request(session, "GET", _ACCOUNT_URL).json()
        except (requests.RequestException, TimeoutError) as exc:
            raise BetterContactUnavailable(f"BetterContact unreachable: {exc}") from exc

    return _as_credit_count(body)


def _as_credit_count(body: dict) -> int:
    """``credits_left`` as a whole number, however the provider spelled it.

    The provider sends it as a **string holding a float** — ``'520.0'`` — so the
    obvious ``isinstance(credits, int)`` rejected every real answer and the balance
    was never once readable: `status` reported `provider_unavailable` against a 200
    that carried the number, and the run's `add_credits` ask could not fire at all.

    Floored rather than rounded, because a fraction of a credit buys nothing. A
    negative count is refused rather than clamped — it is not a balance, it is a
    provider saying something we do not understand.
    """
    raw = body.get("credits_left")
    try:
        credits = int(float(raw))
    except (TypeError, ValueError):
        raise BetterContactUnavailable(
            f"BetterContact returned no credit count: {body!r}") from None
    if credits < 0:
        raise BetterContactUnavailable(f"BetterContact returned a negative balance: {body!r}")
    return credits


def submit(query: BetterContactQuery) -> str:
    """Submit one lookup job to BetterContact; return its ``request_id``.

    Does not wait for a result — the collect leg polls ``request_id`` later via
    ``poll_once``. Raises BetterContactUnavailable when no key is set or the
    service is unreachable (an empty submit included).
    """
    api_key = _require_key()
    with _session(api_key) as session:
        try:
            request_id = _submit(session, _ENRICH_URL, _enrich_body(query))
        except (requests.RequestException, TimeoutError) as exc:
            raise BetterContactUnavailable(f"BetterContact unreachable: {exc}") from exc
    # The find_email block owns the log line — it renders this submit as a step
    # under its ``▶ find_email`` header, so the transport stays quiet here.
    return request_id


def poll_once(request_id: str) -> PollOutcome:
    """Poll one in-flight lookup exactly once — no wait, no retry loop.

    ``running`` while the job is unfinished; a ``hit`` (email set) or a terminal
    ``miss`` once it terminates. The collect leg owns the backoff between calls.
    Raises BetterContactUnavailable when no key is set or the service is
    unreachable.
    """
    api_key = _require_key()
    with _session(api_key) as session:
        try:
            body = _request(session, "GET", f"{_ENRICH_URL}/{request_id}").json()
        except (requests.RequestException, TimeoutError) as exc:
            raise BetterContactUnavailable(f"BetterContact unreachable: {exc}") from exc

    if body.get("status") != "terminated":
        return PollOutcome(running=True)
    rows = body.get("data") or []
    result = _row_to_result(rows[0]) if rows else None
    if result is None:
        return PollOutcome(running=False)
    return PollOutcome(
        running=False,
        email=result.email,
        first_name=result.first_name,
        last_name=result.last_name,
    )


def _require_key() -> str:
    from openoutreach.core.models import SiteConfig

    api_key = SiteConfig.load().bettercontact_api_key
    if not api_key:
        raise BetterContactUnavailable("no BetterContact API key configured")
    return api_key


def _enrich_body(query: BetterContactQuery) -> dict:
    return {
        "data": [{
            "first_name": query.first_name,
            "last_name": query.last_name,
            "company": query.company,
            "company_domain": query.company_domain,
            "linkedin_url": query.linkedin_url,
        }],
        "enrich_email_address": True,
        "enrich_phone_number": False,
    }


# ── shared async transport (used by enrichment + Lead Finder discovery) ───


def submit_and_poll(api_key: str, url: str, body: dict) -> dict:
    """Submit one job to a BetterContact async endpoint, poll until terminated,
    return the terminal JSON body.

    The two BetterContact endpoints — enrichment (`/async`) and Lead Finder
    (`/lead_finder/async`) — share this submit→poll contract; only their request
    body and the key holding the results (`data` vs `leads`) differ, so callers
    pull those out themselves. Raises BetterContactUnavailable on a transport
    failure (HTTP error, network drop, poll timeout) or an empty submit.
    """
    with _session(api_key) as session:
        try:
            request_id = _submit(session, url, body)
            logger.info("%s", step_line(
                "bettercontact", f"req {request_id[:12]}… · poll {_POLL_INTERVAL_S}s ≤{_POLL_TIMEOUT_S}s …"))
            return _poll(session, url, request_id)
        except (requests.RequestException, TimeoutError) as exc:
            raise BetterContactUnavailable(f"BetterContact unreachable: {exc}") from exc


def _session(api_key: str) -> requests.Session:
    """A session that backs off on 429 before any of our code sees the response."""
    session = requests.Session()
    session.headers.update({"X-API-Key": api_key, "User-Agent": _BROWSER_UA})
    adapter = HTTPAdapter(max_retries=_RETRY)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# ── one HTTP call, with the answers the provider actually gives ──


def _request(session: requests.Session, method: str, url: str, **kwargs) -> requests.Response:
    """Perform one request, typing the refusals the provider actually gives.

    The 429 backoff itself lives in the session's transport adapter (``_RETRY``), not
    here — urllib3 already implements exponential backoff that honours ``Retry-After``,
    and a hand-rolled loop would only be a worse copy of it. What is left for this
    function is naming the three refusals, because they are three different things to
    a reader: a bad key, an empty wallet, and *slow down*.
    """
    try:
        resp = session.request(method, url, timeout=_HTTP_TIMEOUT_S, **kwargs)
    except requests.exceptions.RetryError as exc:
        # The adapter exhausted its retries — the only status it retries is 429.
        raise BetterContactUnavailable(
            f"BetterContact rate-limited this client through "
            f"{_RATE_LIMIT_ATTEMPTS} backed-off attempts",
            ErrorType.PROVIDER_RATE_LIMITED,
        ) from exc

    if resp.status_code == 401:
        raise BetterContactUnavailable(
            "BetterContact rejected the API key (401)", ErrorType.PROVIDER_AUTH)
    if resp.status_code == 402:
        raise BetterContactUnavailable(
            "BetterContact credits are exhausted (402)", ErrorType.PROVIDER_OUT_OF_CREDITS)

    resp.raise_for_status()
    return resp


def _submit(session: requests.Session, url: str, body: dict) -> str:
    payload = _request(session, "POST", url, json=body).json()
    request_id = payload.get("request_id") or payload.get("id")
    if not request_id:
        raise BetterContactUnavailable("BetterContact returned no request id")
    return request_id


def _poll(session: requests.Session, url: str, request_id: str) -> dict:
    """Poll until status is terminal; return the terminal JSON body."""
    deadline = time.monotonic() + _POLL_TIMEOUT_S
    attempt = 0
    while True:
        body = _request(session, "GET", f"{url}/{request_id}").json()
        if body.get("status") == "terminated":
            return body
        attempt += 1
        logger.debug("bettercontact: poll %d for %s — status=%s", attempt, request_id, body.get("status"))
        if time.monotonic() >= deadline:
            raise TimeoutError(f"poll timed out for {request_id}")
        time.sleep(_POLL_INTERVAL_S)


def _row_to_result(row: dict) -> BetterContactResult | None:
    email = row.get("contact_email_address")
    status = row.get("contact_email_address_status")
    if email and status in _USABLE_STATUSES:
        return BetterContactResult(
            email=email,
            status=status,
            first_name=row.get("contact_first_name") or None,
            last_name=row.get("contact_last_name") or None,
        )
    return None
