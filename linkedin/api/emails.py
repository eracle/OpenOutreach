# linkedin/api/newsletter.py
from __future__ import annotations

import logging
from typing import Any

import requests
from linkedin.sessions.account import AccountSession

logger = logging.getLogger(__name__)

BREVO_FORM_URL = (
    "https://efe1f107.sibforms.com/serve/"
    "MUIFAEobb1gQ5psA-rFpFReS5VDzoWB-F_AjgYiFptbn9xbYHTSTHDuaRi6gZc_gfhU_r-Qk2ap185L8eAWa6msNWiTmgrc2XClBiA4wQV0pt7J5m02hgTcr0-8v8D1HnWrWnFOa8gaQhJl6VTQySYCZ-JiseHI2ChmwIpkVrvZOMV3LfwQyeTB6TfWcKVzPeAHpCA8TvwCLTMfrjQ=="
)


def add_to_newsletter(email: str, linkedin: str | None = None) -> bool:
    """
    Subscribe email to OpenOutreach newsletter via Brevo form.
    Returns True if successful or already subscribed.
    """
    data = {
        "EMAIL": email,
        "email_address_check": "",  # leave empty (honeypot)
        "locale": "en"
    }
    if linkedin:
        data["LINKEDIN"] = linkedin

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Origin": "https://sibforms.com",
        "Referer": "https://sibforms.com/",
        "Content-Type": "application/x-www-form-urlencoded"
    }

    try:
        r = requests.post(BREVO_FORM_URL, data=data, headers=headers, timeout=10)

        logger.debug("Brevo response: %d - %s", r.status_code, r.text[:200])

        response_lower = r.text.lower()

        if r.status_code == 200:
            if len(r.text.strip()) == 0 or "successful" in response_lower:
                logger.info("Newsletter: successfully added %s", email)
                return True

            if "already subscribed" in response_lower:
                logger.info("Newsletter: already subscribed %s", email)
                return True

        logger.warning(
            "Newsletter subscription failed for %s - status=%d - response: %s",
            email, r.status_code, r.text[:250]
        )
        return False

    except requests.RequestException as e:
        logger.error("Newsletter request failed for %s: %s", email, e)
        return False


def normalize_boolean(value: Any) -> bool | None:
    """Robust boolean normalization from YAML/config values."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in ("true", "t", "yes", "y", "1", "on"):
            return True
        if cleaned in ("false", "f", "no", "n", "0", "off", ""):
            return False
        return None
    if isinstance(value, (int, float)):
        return bool(value)
    return None


def ensure_newsletter_subscription(session: AccountSession):
    """Subscribe the account to the OpenOutreach newsletter if enabled."""
    cfg = session.account_cfg
    handle = session.handle
    subscribe_raw = cfg.get("subscribe_newsletter")
    subscribe = normalize_boolean(subscribe_raw)

    # Case 1: Not set or invalid
    if subscribe is None:
        logger.warning(
            "'subscribe_newsletter' for '%s' is not set properly (value: %r).\n"
            "   Set in assets/accounts.secrets.yaml:\n"
            "     subscribe_newsletter: true   # to join\n"
            "     subscribe_newsletter: false  # to skip",
            handle, subscribe_raw,
        )
        return

    # Case 2: Explicitly false
    if not subscribe:
        logger.debug("Newsletter disabled for %s", handle)
        return

    # Case 3: True → subscribe
    email = cfg.get("username")
    if not email or "@" not in str(email):
        logger.warning("No valid email for newsletter: %s", handle)
        return

    logger.debug("Subscribing %s to OpenOutreach newsletter...", email)
    add_to_newsletter(email)
