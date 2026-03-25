# linkedin/actions/conversations.py
"""Retrieve past LinkedIn conversations for a given profile."""
import logging
from datetime import datetime, timezone

from linkedin.api.client import PlaywrightLinkedinAPI
from linkedin.api.messaging import fetch_conversations, fetch_messages, encode_urn

logger = logging.getLogger(__name__)


def find_conversation_urn(api: PlaywrightLinkedinAPI, target_urn: str, mailbox_urn: str) -> str | None:
    """Find conversation URN for a target profile URN by scanning recent conversations."""
    raw = fetch_conversations(api, mailbox_urn)
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


def parse_message_element(msg: dict) -> dict | None:
    """Parse a single Voyager message element into a dict.

    Returns {entityUrn, text, sender_name, sender_host_urn, delivered_at, is_outgoing (unset)}
    or None if the element should be skipped.
    """
    body = msg.get("body", {})
    text = body.get("text", "") if isinstance(body, dict) else str(body)
    if not text:
        return None

    sender = msg.get("sender", {})
    participant = sender.get("participantType", {}).get("member", {})
    first = (participant.get("firstName") or {}).get("text", "")
    last = (participant.get("lastName") or {}).get("text", "")
    sender_name = f"{first} {last}".strip() or "unknown"

    delivered_at = msg.get("deliveredAt")
    ts = (
        datetime.fromtimestamp(delivered_at / 1000, tz=timezone.utc)
        if delivered_at
        else None
    )

    return {
        "entityUrn": msg.get("entityUrn"),
        "text": text,
        "sender_name": sender_name,
        "sender_host_urn": sender.get("hostIdentityUrn", ""),
        "delivered_at": ts,
    }


def parse_messages(raw: dict) -> list[dict]:
    """Parse raw messages response into a list of {sender, text, timestamp} dicts."""
    elements = raw.get("data", {}).get("messengerMessagesBySyncToken", {}).get("elements", [])

    messages = []
    for msg in elements:
        parsed = parse_message_element(msg)
        if not parsed:
            continue
        ts = parsed["delivered_at"]
        messages.append({
            "sender": parsed["sender_name"],
            "text": parsed["text"],
            "timestamp": ts.strftime("%Y-%m-%d %H:%M") if ts else "",
        })

    messages.sort(key=lambda m: m["timestamp"])
    return messages


def get_conversation(session, target_urn: str, mailbox_urn: str) -> list[dict] | None:
    """Retrieve past messages with a profile.

    Args:
        session: Browser session.
        target_urn: Target profile URN.
        mailbox_urn: Authenticated user's profile URN.

    Returns a list of {sender, text, timestamp} dicts, or None if no conversation exists.
    """
    session.ensure_browser()
    api = PlaywrightLinkedinAPI(session=session)

    conversation_urn = find_conversation_urn(api, target_urn, mailbox_urn)
    if not conversation_urn:
        logger.debug("Not in recent conversations, trying navigation fallback")
        conversation_urn = find_conversation_urn_via_navigation(session, target_urn)
    if not conversation_urn:
        logger.info("No conversation found for %s", target_urn)
        return None

    raw = fetch_messages(api, conversation_urn)
    return parse_messages(raw)


if __name__ == "__main__":
    from crm.models import Lead
    from linkedin.browser.registry import cli_parser, cli_session

    parser = cli_parser("Retrieve LinkedIn conversation history")
    parser.add_argument("--profile", required=True, help="Public identifier of target profile")
    args = parser.parse_args()
    session = cli_session(args)

    print(f"Fetching conversation as {session} → {args.profile}")
    lead = Lead.objects.get(public_identifier=args.profile)
    target_urn = lead.get_urn(session)
    mailbox_urn = session.self_profile["urn"]
    messages = get_conversation(session, target_urn, mailbox_urn)

    if messages is None:
        print(f"No conversation found with {args.profile}")
    elif not messages:
        print("Conversation found but no messages parsed")
    else:
        print(f"\n--- Conversation with {args.profile} ({len(messages)} messages) ---\n")
        for msg in messages:
            print(f"[{msg['timestamp']}] {msg['sender']}: {msg['text']}\n")
