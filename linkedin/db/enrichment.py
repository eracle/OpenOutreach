import logging
from typing import Any

from linkedin.db.leads import lead_profile_by_id

logger = logging.getLogger(__name__)


def ensure_lead_enriched(session, lead_id: int, public_id: str) -> bool:
    """Lazily enrich a url-only Lead via Voyager API (robustness fallback).

    Kept for robustness — normal flow enriches eagerly at discovery time
    (see _enrich_new_urls). Should rarely fire in practice.

    No-op (returns True) when the lead already has a description.
    Returns False when enrichment is not possible (API error, private profile,
    or missing lead).
    """
    from crm.models import Lead
    from linkedin.db.leads import _update_lead_fields, _ensure_company, _attach_raw_data

    lead = Lead.objects.filter(pk=lead_id).first()
    if not lead:
        return False
    if lead.description:
        return True

    profile, data = _fetch_profile(session, public_id)
    if not profile:
        return False

    _update_lead_fields(lead, profile)
    _ensure_company(lead, profile)
    if data:
        _attach_raw_data(lead, public_id, data)

    logger.warning("Lazy-enriched %s (lead_id=%d) — should already have been enriched at discovery", public_id, lead_id)
    return True


def ensure_profile_embedded(lead_id: int, public_id: str, session) -> bool:
    """Lazily enrich + embed a Lead as a single operation (robustness fallback).

    Kept for robustness — normal flow embeds eagerly at discovery time
    (see create_enriched_lead). Should rarely fire in practice.

    No-op (returns True) when the embedding already exists.
    Url-only leads are enriched via Voyager API before embedding.
    Returns False when embedding is not possible.
    """
    from linkedin.models import ProfileEmbedding

    if ProfileEmbedding.objects.filter(lead_id=lead_id).exists():
        return True

    profile_data = lead_profile_by_id(lead_id)
    if not profile_data:
        if not ensure_lead_enriched(session, lead_id, public_id):
            return False
        profile_data = lead_profile_by_id(lead_id)
        if not profile_data:
            return False

    from linkedin.ml.embeddings import embed_profile

    logger.warning("Lazy-embedded %s (lead_id=%d) — should already have been embedded at discovery", public_id, lead_id)
    return embed_profile(lead_id, public_id, profile_data)


def load_embedding(lead_id: int, public_id: str, session):
    """Load embedding array, lazily enriching+embedding if needed.

    The embedding should already exist from eager discovery. The lazy
    fallback is kept for robustness and should rarely fire in practice.
    """
    from linkedin.models import ProfileEmbedding

    ensure_profile_embedded(lead_id, public_id, session)
    row = ProfileEmbedding.objects.filter(lead_id=lead_id).first()
    return row.embedding_array if row else None


def _fetch_profile(session, public_id: str) -> tuple[dict, Any] | tuple[None, None]:
    """Call Voyager API for a single profile. Returns (profile, raw_data) or (None, None)."""
    from linkedin.api.client import PlaywrightLinkedinAPI

    session.ensure_browser()
    api = PlaywrightLinkedinAPI(session=session)
    try:
        return api.get_profile(public_identifier=public_id)
    except Exception:
        logger.warning("Voyager API failed for %s", public_id)
        return None, None
