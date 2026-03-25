import logging


logger = logging.getLogger(__name__)


def _get_lead_and_ct(public_identifier: str):
    """Return (lead, content_type) for a public identifier."""
    from django.contrib.contenttypes.models import ContentType
    from crm.models import Lead

    lead = Lead.objects.get(public_identifier=public_identifier)
    ct = ContentType.objects.get_for_model(lead)
    return lead, ct


def sync_conversation(session, public_identifier: str) -> list[dict]:
    """Fetch messages from Voyager API and upsert into ChatMessage.

    Returns messages as a list of {sender, text, timestamp, is_outgoing} dicts
    from the DB (always the source of truth after sync).
    """
    lead, ct = _get_lead_and_ct(public_identifier)
    _sync_from_api(session, public_identifier, lead, ct)

    return _read_from_db(public_identifier)


def _sync_from_api(session, public_identifier: str, lead, ct):
    """Fetch messages from Voyager API and upsert into DB."""
    from chat.models import ChatMessage
    from linkedin.actions.conversations import (
        find_conversation_urn, find_conversation_urn_via_navigation, parse_message_element,
    )
    from linkedin.api.client import PlaywrightLinkedinAPI
    from linkedin.api.messaging import fetch_messages

    session.ensure_browser()
    api = PlaywrightLinkedinAPI(session=session)

    target_urn = lead.get_urn(session)
    mailbox_urn = session.self_profile["urn"]

    # Find conversation URN
    conversation_urn = find_conversation_urn(api, target_urn, mailbox_urn)
    if not conversation_urn:
        conversation_urn = find_conversation_urn_via_navigation(session, target_urn)
    if not conversation_urn:
        logger.debug("sync: no conversation found for %s", public_identifier)
        return

    # Fetch messages
    raw = fetch_messages(api, conversation_urn)
    elements = raw.get("data", {}).get("messengerMessagesBySyncToken", {}).get("elements", [])

    self_urn = session.self_profile["urn"]

    for msg in elements:
        parsed = parse_message_element(msg)
        if not parsed or not parsed["entityUrn"]:
            continue

        is_outgoing = parsed["sender_host_urn"] == self_urn

        # Upsert by linkedin_urn
        _, created = ChatMessage.objects.update_or_create(
            linkedin_urn=parsed["entityUrn"],
            defaults={
                "content_type": ct,
                "object_id": lead.pk,
                "content": parsed["text"],
                "is_outgoing": is_outgoing,
                "owner": session.django_user,
                **({"creation_date": parsed["delivered_at"]} if parsed["delivered_at"] else {}),
            },
        )
        if created:
            logger.debug("sync: new message from %s for %s", parsed["sender_name"], public_identifier)

    logger.debug("sync: processed %d messages for %s", len(elements), public_identifier)


def _read_from_db(public_identifier: str) -> list[dict]:
    """Read all ChatMessages for a lead, sorted chronologically."""
    from chat.models import ChatMessage

    lead, ct = _get_lead_and_ct(public_identifier)
    lead_name = f"{lead.first_name or ''} {lead.last_name or ''}".strip() or "them"

    messages = ChatMessage.objects.filter(
        content_type=ct, object_id=lead.pk,
    ).select_related("owner").order_by("creation_date")

    result = []
    for msg in messages:
        if not msg.content:
            continue
        if msg.is_outgoing:
            owner = msg.owner
            sender = f"{owner.first_name or ''} {owner.last_name or ''}".strip() if owner else "me"
        else:
            sender = lead_name
        result.append({
            "sender": sender or "me",
            "text": msg.content,
            "timestamp": msg.creation_date.strftime("%Y-%m-%d %H:%M") if msg.creation_date else "",
            "is_outgoing": msg.is_outgoing,
        })
    return result
