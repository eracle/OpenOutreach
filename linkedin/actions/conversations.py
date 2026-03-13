# linkedin/actions/conversations.py
"""Retrieve past LinkedIn conversations for a given profile."""
import logging
from datetime import datetime

from linkedin.api.client import PlaywrightLinkedinAPI
from linkedin.api.messaging import fetch_conversations, fetch_messages, encode_urn

logger = logging.getLogger(__name__)


def find_conversation_urn(api: PlaywrightLinkedinAPI, target_urn: str) -> str | None:
    """Find conversation URN for a target profile URN by scanning recent conversations."""
    raw = fetch_conversations(api)
    elements = raw.get("data", {}).get("messengerConversationsBySyncToken", {}).get("elements", [])

    for conv in elements:
        for p in conv.get("conversationParticipants", []):
            if p.get("hostIdentityUrn") == target_urn:
                return conv.get("entityUrn")
    return None


def find_conversation_urn_via_navigation(session, target_urn: str) -> str | None:
    """Navigate to the messaging page for a profile and capture the conversation URN.

    Works for older conversations not in the first page of API results.
    """
    page = session.page
    captured_urn = [None]

    def on_response(response):
        if "messengerMessages" not in response.url:
            return
        try:
            data = response.json()
            elements = data.get("data", {}).get("messengerMessagesBySyncToken", {}).get("elements", [])
            if elements:
                captured_urn[0] = elements[0].get("conversation", {}).get("entityUrn")
        except Exception:
            pass

    session.context.on("response", on_response)
    try:
        url = f"https://www.linkedin.com/messaging/thread/new/?recipient={encode_urn(target_urn)}"
        logger.debug("Navigating to messaging thread → %s", url)
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(8_000)
    except Exception as e:
        logger.warning("Navigation to messaging thread failed: %s", e)
    finally:
        session.context.remove_listener("response", on_response)

    return captured_urn[0]


def parse_messages(raw: dict) -> list[dict]:
    """Parse raw messages response into a list of {sender, text, timestamp} dicts."""
    elements = raw.get("data", {}).get("messengerMessagesBySyncToken", {}).get("elements", [])

    messages = []
    for msg in elements:
        body = msg.get("body", {})
        text = body.get("text", "") if isinstance(body, dict) else str(body)
        if not text:
            continue

        participant = msg.get("sender", {}).get("participantType", {}).get("member", {})
        first = (participant.get("firstName") or {}).get("text", "")
        last = (participant.get("lastName") or {}).get("text", "")
        sender_name = f"{first} {last}".strip()

        delivered_at = msg.get("deliveredAt")
        ts = datetime.fromtimestamp(delivered_at / 1000).strftime("%Y-%m-%d %H:%M") if delivered_at else ""

        messages.append({"sender": sender_name or "unknown", "text": text, "timestamp": ts})

    messages.sort(key=lambda m: m["timestamp"])
    return messages


def get_conversation(session, public_identifier: str) -> list[dict] | None:
    """Retrieve past messages with a profile.

    Returns a list of {sender, text, timestamp} dicts, or None if no conversation exists.
    """
    from linkedin.db.leads import resolve_urn

    session.ensure_browser()
    api = PlaywrightLinkedinAPI(session=session)

    target_urn = resolve_urn(public_identifier, session=session)
    if not target_urn:
        logger.warning("Could not resolve URN for %s", public_identifier)
        return None

    conversation_urn = find_conversation_urn(api, target_urn)
    if not conversation_urn:
        logger.debug("Not in recent conversations, trying navigation fallback")
        conversation_urn = find_conversation_urn_via_navigation(session, target_urn)
    if not conversation_urn:
        logger.info("No conversation found for %s", public_identifier)
        return None

    raw = fetch_messages(api, conversation_urn)
    return parse_messages(raw)


if __name__ == "__main__":
    import os
    import argparse

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "linkedin.django_settings")

    import django
    django.setup()

    from linkedin.conf import get_first_active_profile_handle
    from linkedin.browser.registry import get_or_create_session

    logging.basicConfig(level=logging.DEBUG, format="[%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="Retrieve LinkedIn conversation history")
    parser.add_argument("--handle", default=None)
    parser.add_argument("--profile", required=True, help="Public identifier of target profile")
    args = parser.parse_args()

    handle = args.handle or get_first_active_profile_handle()
    if not handle:
        print("No active LinkedInProfile found.")
        raise SystemExit(1)

    session = get_or_create_session(handle=handle)
    session.campaign = session.campaigns.first()

    print(f"Fetching conversation as @{handle} → {args.profile}")
    messages = get_conversation(session, args.profile)

    if messages is None:
        print(f"No conversation found with {args.profile}")
    elif not messages:
        print("Conversation found but no messages parsed")
    else:
        print(f"\n--- Conversation with {args.profile} ({len(messages)} messages) ---\n")
        for msg in messages:
            print(f"[{msg['timestamp']}] {msg['sender']}: {msg['text']}\n")
