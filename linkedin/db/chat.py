import logging

from linkedin.db.urls import public_id_to_url

logger = logging.getLogger(__name__)


def save_chat_message(session: "AccountSession", public_identifier: str, content: str):
    """Persist an outgoing message as a ChatMessage attached to the Lead."""
    from chat.models import ChatMessage
    from django.contrib.contenttypes.models import ContentType
    from crm.models import Lead

    clean_url = public_id_to_url(public_identifier)
    lead = Lead.objects.filter(website=clean_url).first()
    if not lead:
        logger.warning("save_chat_message: no Lead for %s", public_identifier)
        return

    ct = ContentType.objects.get_for_model(lead)
    ChatMessage.objects.create(
        content_type=ct,
        object_id=lead.pk,
        content=content,
        owner=session.django_user,
    )
    logger.debug("Saved chat message for %s", public_identifier)
