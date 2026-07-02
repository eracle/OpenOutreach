# openoutreach/emails/newsletter.py
"""OpenOutreach newsletter subscription — a plain Brevo form POST.

Subscribes the operator's own email (their connected mailbox address) to the
OpenOutreach newsletter. Nothing here touches LinkedIn: the subscription was
always an email signup; only the country-derived opt-in default and an optional
LinkedIn field used to ride along, and both are gone.
"""
from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

BREVO_FORM_URL = (
    "https://efe1f107.sibforms.com/serve/"
    "MUIFAEobb1gQ5psA-rFpFReS5VDzoWB-F_AjgYiFptbn9xbYHTSTHDuaRi6gZc_gfhU_r-Qk2ap185L8eAWa6msNWiTmgrc2XClBiA4wQV0pt7J5m02hgTcr0-8v8D1HnWrWnFOa8gaQhJl6VTQySYCZ-JiseHI2ChmwIpkVrvZOMV3LfwQyeTB6TfWcKVzPeAHpCA8TvwCLTMfrjQ=="
)


def subscribe_to_newsletter(email: str) -> bool:
    """Subscribe *email* to the OpenOutreach newsletter via the Brevo form.

    Returns True if newly added or already subscribed, False on any failure.
    """
    data = {
        "EMAIL": email,
        "email_address_check": "",  # honeypot — leave empty
        "locale": "en",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Origin": "https://sibforms.com",
        "Referer": "https://sibforms.com/",
        "Content-Type": "application/x-www-form-urlencoded",
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
            email, r.status_code, r.text[:250],
        )
        return False
    except requests.RequestException as e:
        logger.error("Newsletter request failed for %s: %s", email, e)
        return False
