# linkedin/api/messaging/send.py
"""Send messages via Voyager Messaging API."""
import json
import logging
import os
import uuid
from typing import Optional

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from linkedin.api.client import PlaywrightLinkedinAPI
from linkedin.api.messaging.utils import get_self_urn, check_response

logger = logging.getLogger(__name__)


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
    """
    if not mailbox_urn:
        mailbox_urn = get_self_urn(api)

    origin_token = str(uuid.uuid4())
    tracking_id = os.urandom(16).hex()

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

    headers = {**api.headers}
    headers["accept"] = "application/json"
    headers["content-type"] = "text/plain;charset=UTF-8"

    logger.debug("Voyager send_message → %s", conversation_urn)

    res = api.post(url, headers=headers, data=json.dumps(payload))
    check_response(res, "send_message")

    data = res.json()
    delivered_at = data.get("value", {}).get("deliveredAt")
    logger.info("Message delivered → %s (at %s)", conversation_urn, delivered_at)
    return data


if __name__ == "__main__":
    import os
    import argparse

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "linkedin.django_settings")

    import django
    django.setup()

    from linkedin.conf import get_first_active_profile_handle
    from linkedin.browser.registry import get_or_create_session
    from linkedin.db.leads import resolve_urn
    from linkedin.actions.conversations import find_conversation_urn, find_conversation_urn_via_navigation

    logging.basicConfig(level=logging.DEBUG, format="[%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="Send a message via Voyager Messaging API")
    parser.add_argument("--handle", default=None)
    parser.add_argument("--profile", required=True, help="Public identifier of target profile")
    parser.add_argument("--text", required=True, help="Message text to send")
    args = parser.parse_args()

    handle = args.handle or get_first_active_profile_handle()
    if not handle:
        print("No active LinkedInProfile found.")
        raise SystemExit(1)

    session = get_or_create_session(handle=handle)
    session.campaign = session.campaigns.first()
    session.ensure_browser()

    api = PlaywrightLinkedinAPI(session=session)

    # Resolve target profile URN
    target_urn = resolve_urn(args.profile, session=session)
    if not target_urn:
        print(f"Could not resolve URN for {args.profile}")
        raise SystemExit(1)
    print(f"Resolved URN: {target_urn}")

    # Find conversation URN
    conversation_urn = find_conversation_urn(api, target_urn)
    if not conversation_urn:
        print("Not in recent conversations, trying navigation fallback...")
        conversation_urn = find_conversation_urn_via_navigation(session, target_urn)
    if not conversation_urn:
        print(f"No existing conversation found with {args.profile}")
        raise SystemExit(1)
    print(f"Conversation URN: {conversation_urn}")

    # Send message via API
    print(f"Sending message to {args.profile}: {args.text}")
    result = send_message(api, conversation_urn, args.text)
    delivered_at = result.get("value", {}).get("deliveredAt")
    print(f"Message sent successfully! (deliveredAt: {delivered_at})")
