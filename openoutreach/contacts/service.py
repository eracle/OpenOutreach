# openoutreach/contacts/service.py
"""The central contacts store (the hub) — ask the hub before paying BetterContact,
give back what we find.

Two best-effort calls; a missing token or an outage degrades to a no-op and never
breaks outreach. The store caches ``public_identifier -> email`` so the network's
paid + harvested resolutions lower everyone's BetterContact spend as coverage grows.

The geo-gate that keeps EEA/UK/CH out of the store is enforced **server-side** (the
only trusted boundary). The cheap ``is_eea_located`` check here just avoids a
pointless round-trip for a lead we already know is out of scope — it reads the
lead's own ``country_code`` (persisted at discovery), so there is no extra scrape.
"""
from __future__ import annotations

import logging

import requests

from openoutreach.core.models import SiteConfig
from openoutreach.core.operator import get_active_user
from openoutreach.core.geo import is_eea_located
from openoutreach.core import version

logger = logging.getLogger(__name__)

DEFAULT_API_URL = "https://hub.openoutreach.app"
_TIMEOUT_S = 30

# Where a contributed address came from — the wire values the hub maps to its
# Contribution.Origin (an unrecognized value degrades to "unknown" server-side).
ORIGIN_BETTERCONTACT = "bettercontact"  # paid BetterContact hit
ORIGIN_APOLLO = "apollo"  # paid Apollo people/match hit
ORIGIN_PROFILE_INFO = "profile_info"  # 1st-degree contact-info overlay

# These match ``enrichment.provider``'s module ``NAME``s, which is what ``lookup``
# actually passes — a finder stamps its own contributions and no caller maps between
# the two vocabularies. **The hub needs the matching Contribution.Origin entry**, or
# an Apollo give-back degrades to "unknown" server-side (still stored, just unlabelled).


def resolve(lead) -> str | None:
    """A stored email for *lead*, or ``None`` — a miss, no token yet, or an
    outage all return ``None``, so the caller falls back to BetterContact."""
    config = SiteConfig.load()
    if not config.contacts_api_token:
        return None
    try:
        resp = requests.get(
            _endpoint(config, "resolve"),
            params={"id": lead.profile_url},
            headers=_auth(config.contacts_api_token),
            timeout=_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        logger.info("hub: resolve unavailable for %s: %s", lead.profile_url, exc)
        return None
    if resp.status_code not in (200, 404):
        return None  # unexpected → fall back to BetterContact, stay quiet
    # Both hit (200) and miss (404) carry the post-read credit balance; a hit
    # also carries the profile's address(es) as a list (one today, the full
    # dbt-prepared set later), and we send to one, so take the first.
    payload = resp.json()
    credits = payload.get("credits")
    emails = payload.get("emails") or []
    email = emails[0] if emails else None
    if email:
        logger.info("hub: resolved %s for %s (saved a paid lookup) — %s credits available",
                    email, lead.profile_url, credits)
    elif credits is not None and credits <= 0:
        logger.info("hub: no balance to read the store for %s — falling back to BetterContact "
                    "(contribute an address to earn a read)", lead.profile_url)
    else:
        logger.info("hub: no stored email for %s — falling back to BetterContact (store balance: %s credits)",
                    lead.profile_url, credits)
    return email


def contribute(lead, emails: list[str], origin: str) -> None:
    """Give *lead*'s email(s) to the store — best-effort, non-EU only.

    ``origin`` records where the address came from (``ORIGIN_BETTERCONTACT`` /
    ``ORIGIN_PROFILE_INFO``). The first contribution registers and mints the
    operator's token (kept in the instance's own config, never the repo); later
    ones reuse it.

    Honors the operator's jurisdiction: an EEA/UK/CH operator does not contribute
    (derived from their onboarding country, ``not is_eea_located``), so the whole
    give-back is skipped (no email, no vector — and so no give-to-get credit).
    """
    from openoutreach.core.models import SiteConfig

    if is_eea_located(SiteConfig.load().country_code):
        logger.debug("hub: operator in EEA/UK/CH — skipping give-back for %s", lead.profile_url)
        return
    emails = [e for e in emails if e]
    if not emails:
        logger.debug("hub: nothing to contribute for %s — no email captured", lead.profile_url)
        return
    if is_eea_located(lead.country_code):
        logger.debug("hub: skipping %s (%s) — EEA/UK/CH lead, out of store scope",
                     lead.profile_url, lead.country_code)
        return

    config = SiteConfig.load()
    record = {
        "public_identifier": lead.profile_url,
        "country_code": lead.country_code,
        "emails": emails,
        "origin": origin,
        **_build_fields(),
    }
    _attach_embedding(lead, record)
    if config.contacts_api_token:
        _send(config, "contribute", record, lead, headers=_auth(config.contacts_api_token))
    else:
        _register(config, record, lead)


def _attach_embedding(lead, record: dict) -> None:
    """Add the cached profile vector to *record*, in place, when it's in hand.

    The operator's opt-in is already checked in ``contribute``, so this only asks
    whether a vector exists. Reads the cached bytes (``lead.embedding``) — never
    ``get_embedding``, which would re-scrape — so a lead that was never embedded
    contributes nothing extra. The 384 floats go on the wire as a JSON list; the
    hub packs them to f16 bytes and validates the length.
    """
    if lead.embedding is None:
        return
    record["embedding"] = lead.embedding_array.tolist()


def register_operator() -> bool:
    """Mint + persist this install's hub token from the operator's email alone.

    **Identity is not entitlement.** The token says *which install this is*; the
    balance says what it may read. They used to be the same act — a token was minted
    only as a side effect of a first contribution — which meant an install that
    cannot contribute had no identity at all and stayed invisible to the hub for its
    whole life. Every later idea needs the identity and none of them need the
    contribution: quotas, revocation, per-install metering, showing an operator their
    own balance, and any starter-balance experiment.

    Called from onboarding, where the email is already collected, so there is **no
    new question to ask**. Runs regardless of jurisdiction — the EEA/UK/CH rule is
    about *contributing records*, which is a different act and still gated in
    ``contribute``.

    Best-effort and idempotent: an install that already holds a token does nothing, a
    hub outage is a no-op the next run retries, and re-registering the same email
    returns the same token. Returns whether a token is in hand afterwards.

    Carries the build sha, so which version an install runs is known from its first
    minute rather than from its first contribution.

    *(This is not marketing consent. The newsletter opt-in in onboarding is that, and
    it is jurisdiction-aware. Keep the two separate.)*
    """
    from openoutreach.core.models import SiteConfig

    config = SiteConfig.load()
    if config.contacts_api_token:
        return True

    email = get_active_user().email
    if not email:
        logger.debug("hub: no operator email yet — nothing to register")
        return False

    # The build rides along: for an install that never contributes, this is the only
    # time it ever names the version it runs.
    return _mint(config, {"operator_email": email, **_build_fields()})


def hub_balance() -> dict:
    """This install's give-to-get balance, read without spending it.

    Piggybacks on ``register``: a record-less call is idempotent
    (``ApiToken.objects.get_or_create``) and already returns ``credits``, so a repeat
    call reuses the existing token and costs nothing. Best-effort like every other hub
    call — no token yet, or an outage, both report *unknown*, never a balance of zero,
    since the two must not look alike to a caller deciding whether to explain the
    store as empty or as closed.
    """
    config = SiteConfig.load()
    if not config.contacts_api_token:
        return {"balance": None, "known": False}

    email = get_active_user().email
    if not email:
        return {"balance": None, "known": False}

    payload = _send(config, "register", {"operator_email": email, **_build_fields()},
                    headers=_auth(config.contacts_api_token))
    if payload is None or "credits" not in payload:
        return {"balance": None, "known": False}
    return {"balance": payload["credits"], "known": True}


def _register(config: SiteConfig, record: dict, lead) -> None:
    """Mint the token by folding it into a first contribution.

    The compatibility path, and the only one a hub that still requires a record will
    accept. ``register_operator`` is the one that should normally have run, at
    onboarding; this catches the install whose hub was down that day and which has now
    reached a contribution anyway.
    """
    _mint(config, {"operator_email": get_active_user().email, **record}, lead)


def _mint(config: SiteConfig, body: dict, lead=None) -> bool:
    """POST to ``register`` and persist whatever token comes back."""
    response = _send(config, "register", body, lead)
    token = response.get("token") if response else None
    if not token:
        return False
    config.contacts_api_token = token
    config.save(update_fields=["contacts_api_token"])
    logger.info("hub: registered — API token stored")
    return True


def _send(config: SiteConfig, path: str, body: dict, lead=None,
          headers: dict | None = None) -> dict | None:
    """POST one body; log + swallow any transport failure. ``None`` on failure.

    ``lead`` is the record's subject when there is one. A register that carries no
    record has none, and must not be narrated as a contribution — it gave nothing,
    and the hub may answer it without a ``credits`` field at all.
    """
    try:
        resp = requests.post(_endpoint(config, path), json=body,
                             headers=headers or _headers(), timeout=_TIMEOUT_S)
        resp.raise_for_status()
    except requests.RequestException as exc:
        subject = lead.profile_url if lead is not None else "operator identity"
        logger.info("hub: unavailable for %s: %s", subject, exc)
        return None

    payload = resp.json()
    if lead is not None:
        logger.info("hub: contributed %s (%s) to the central store — %s credits available",
                    lead.profile_url, lead.country_code, payload.get("credits"))
    return payload


def _endpoint(config: SiteConfig, path: str) -> str:
    base = config.contacts_api_url or DEFAULT_API_URL
    return f"{base.rstrip('/')}/api/v2/{path}/"


def _auth(token: str) -> dict:
    return {**_headers(), "Authorization": f"Bearer {token}"}


def _headers() -> dict:
    """Headers every hub call carries, authenticated or not.

    The product token names the build (``OpenOutreach/2026.08.07+g947927d``), so
    even a request that never reaches a stored row — a ``resolve`` miss — still
    says which code asked."""
    return {"User-Agent": version.user_agent()}


def _build_fields() -> dict:
    """Which build produced this record, for the hub to resolve to a release.

    The sha is the identity; the hub decides whether it belongs to the published
    history (and what its date is), because that verdict must not be the client's
    to make. ``client_dirty`` is omitted when undetermined rather than sent as a
    reassuring ``False``."""
    fields = {"client_sha": version.commit_sha()}
    dirty = version.is_dirty()
    if dirty is not None:
        fields["client_dirty"] = dirty
    return fields
