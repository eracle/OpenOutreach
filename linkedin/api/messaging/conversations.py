# linkedin/api/messaging/conversations.py
"""Retrieve conversations and messages via Voyager Messaging GraphQL API."""
import logging

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from linkedin.api.client import PlaywrightLinkedinAPI
from linkedin.api.messaging.utils import encode_urn, check_response

logger = logging.getLogger(__name__)

_GRAPHQL_BASE = "https://www.linkedin.com/voyager/api/voyagerMessagingGraphQL/graphql"
_CONVERSATIONS_QUERY_ID = "messengerConversations.0d5e6781bbee71c3e51c8843c6519f48"
_MESSAGES_QUERY_ID = "messengerMessages.5846eeb71c981f11e0134cb6626cc314"


def _graphql_headers(api: PlaywrightLinkedinAPI) -> dict:
    headers = {**api.headers}
    headers["accept"] = "application/graphql"
    return headers


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    retry=retry_if_exception_type(IOError),
    reraise=True,
)
def fetch_conversations(api: PlaywrightLinkedinAPI) -> dict:
    """Fetch recent conversations list. Returns raw API response."""
    mailbox_urn = api.session.self_profile["urn"]
    url = (
        f"{_GRAPHQL_BASE}"
        f"?queryId={_CONVERSATIONS_QUERY_ID}"
        f"&variables=(mailboxUrn:{encode_urn(mailbox_urn)})"
    )
    res = api.get(url, headers=_graphql_headers(api))
    check_response(res, "fetch_conversations")
    return res.json()


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    retry=retry_if_exception_type(IOError),
    reraise=True,
)
def fetch_messages(api: PlaywrightLinkedinAPI, conversation_urn: str) -> dict:
    """Fetch messages for a conversation. Returns raw API response."""
    url = (
        f"{_GRAPHQL_BASE}"
        f"?queryId={_MESSAGES_QUERY_ID}"
        f"&variables=(conversationUrn:{encode_urn(conversation_urn)})"
    )
    res = api.get(url, headers=_graphql_headers(api))
    check_response(res, "fetch_messages")
    return res.json()


if __name__ == "__main__":
    import os
    import argparse
    import json

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "linkedin.django_settings")

    import django
    django.setup()

    from linkedin.conf import resolve_profile
    from linkedin.browser.registry import get_or_create_session

    logging.basicConfig(level=logging.DEBUG, format="[%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="Fetch raw Voyager messaging data")
    parser.add_argument("--handle", default=None, help="Django username (default: first active)")
    parser.add_argument("--conversations", action="store_true", help="List recent conversations")
    parser.add_argument("--messages", default=None, metavar="CONVERSATION_URN", help="Fetch messages for a conversation URN")
    args = parser.parse_args()

    linkedin_profile = resolve_profile(args.handle)
    if not linkedin_profile:
        print("No active LinkedInProfile found.")
        raise SystemExit(1)

    session = get_or_create_session(linkedin_profile)
    session.campaign = session.campaigns[0]
    session.ensure_browser()

    api = PlaywrightLinkedinAPI(session=session)

    if args.conversations:
        raw = fetch_conversations(api)
        elements = raw.get("data", {}).get("messengerConversationsBySyncToken", {}).get("elements", [])
        print(f"Got {len(elements)} conversations:\n")
        for conv in elements:
            urn = conv.get("entityUrn", "")
            participants = []
            for p in conv.get("conversationParticipants", []):
                member = p.get("participantType", {}).get("member", {})
                first = (member.get("firstName") or {}).get("text", "")
                last = (member.get("lastName") or {}).get("text", "")
                name = f"{first} {last}".strip()
                if name:
                    participants.append(name)
            print(f"  {', '.join(participants)}")
            print(f"    URN: {urn}\n")

    elif args.messages:
        raw = fetch_messages(api, args.messages)
        print(json.dumps(raw, indent=2, default=str)[:10000])

    else:
        parser.print_help()
