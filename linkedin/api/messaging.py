# linkedin/api/messaging.py
"""Voyager Messaging API — send messages via LinkedIn's internal API."""
import base64
import json
import logging
import os
import uuid
from typing import Optional

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from linkedin.api.client import PlaywrightLinkedinAPI, REQUEST_TIMEOUT_MS
from linkedin.navigation.exceptions import AuthenticationError

logger = logging.getLogger(__name__)


def _get_self_urn(api: PlaywrightLinkedinAPI) -> str:
    """Return the authenticated user's fsd_profile URN."""
    profile, _ = api.get_profile(public_identifier="me")
    if not profile:
        raise AuthenticationError("Cannot fetch own profile via Voyager API")
    return profile["urn"]


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    retry=retry_if_exception_type(IOError),
    reraise=True,
)
def send_message(
        api: PlaywrightLinkedinAPI,
        conversation_urn: str,
        message_text: str,
        mailbox_urn: Optional[str] = None,
) -> dict:
    """Send a message via Voyager Messaging API.

    Args:
        api: Authenticated PlaywrightLinkedinAPI instance.
        conversation_urn: e.g. "urn:li:msg_conversation:(urn:li:fsd_profile:XXX,2-threadId)"
        message_text: The message body.
        mailbox_urn: Sender's profile URN. Auto-discovered from /in/me/ if omitted.

    Returns:
        API response dict with delivery confirmation.

    TODO: conversation_urn discovery — need a way to resolve a recipient's
          profile URN (or public_id) into a conversation_urn. Likely another
          Voyager endpoint or constructable from both profile URNs.
    """
    if not mailbox_urn:
        mailbox_urn = _get_self_urn(api)

    origin_token = str(uuid.uuid4())
    tracking_id = base64.b64encode(os.urandom(16)).decode("ascii")

    payload = {
        "message": {
            "body": {
                "attributes": [],
                "text": message_text,
            },
            "renderContentUnions": [],
            "conversationUrn": conversation_urn,
            "originToken": origin_token,
        },
        "mailboxUrn": mailbox_urn,
        "trackingId": tracking_id,
        "dedupeByClientGeneratedToken": False,
    }

    url = (
        "https://www.linkedin.com/voyager/api"
        "/voyagerMessagingDashMessengerMessages?action=createMessage"
    )

    # Messaging endpoint uses different accept + content-type than profile API
    headers = {**api.headers}
    headers["accept"] = "application/json"
    headers["content-type"] = "text/plain;charset=UTF-8"

    logger.debug("Voyager send_message → %s", conversation_urn)

    res = api.context.request.post(
        url, data=json.dumps(payload), headers=headers,
        timeout=REQUEST_TIMEOUT_MS,
    )

    match res.status:
        case 401:
            logger.error("Messaging API → 401 Unauthorized")
            raise AuthenticationError("LinkedIn Messaging API returned 401.")

        case 403 | 404:
            logger.warning("Messaging API → %d for %s", res.status, conversation_urn)
            raise IOError(f"LinkedIn Messaging API returned {res.status}")

    if not res.ok:
        body_str = (
            res.body().decode("utf-8", errors="ignore")
            if isinstance(res.body(), bytes) else str(res.body())
        )
        raise IOError(f"LinkedIn Messaging API error {res.status}: {body_str[:500]}")

    data = res.json()
    delivered_at = data.get("value", {}).get("deliveredAt")
    logger.info("Message delivered → %s (at %s)", conversation_urn, delivered_at)
    return data
