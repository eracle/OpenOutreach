import logging

from linkedin.db.leads import lead_profile_by_id
from linkedin.exceptions import AuthenticationError

logger = logging.getLogger(__name__)


def ensure_lead_enriched(session, lead_id: int, public_id: str, *, quiet: bool = False) -> bool:
    """Lazily enrich a url-only Lead via Voyager API (robustness fallback).

    Kept for robustness — normal flow enriches eagerly at discovery time
    (see _enrich_new_urls). Should rarely fire in practice.

    No-op (returns True) when the lead already has a description.
    Returns False when enrichment is not possible (API error, private profile,
    or missing lead).
    """
    from crm.models import Lead
    from linkedin.db.leads import _update_lead_fields

    lead = Lead.objects.filter(pk=lead_id).first()
    if not lead:
        return False
    if lead.description:
        return True

    profile = _fetch_profile(session, public_id)
    if not profile:
        return False

    _update_lead_fields(lead, profile)

    if not quiet:
        logger.warning("Lazy-enriched %s (lead_id=%d) — should already have been enriched at discovery", public_id, lead_id)
    return True


def ensure_profile_embedded(lead_id: int, public_id: str, session, *, quiet: bool = False) -> bool:
    """Lazily enrich + embed a Lead as a single operation (robustness fallback).

    Kept for robustness — normal flow embeds eagerly at discovery time
    (see create_enriched_lead). Should rarely fire in practice.

    No-op (returns True) when the embedding already exists.
    Url-only leads are enriched via Voyager API before embedding.
    Returns False when embedding is not possible.
    """
    from crm.models import Lead

    lead = Lead.objects.filter(pk=lead_id).only("embedding", "description").first()
    if lead and lead.embedding is not None:
        return True

    profile_data = lead_profile_by_id(lead_id)
    if not profile_data:
        if not ensure_lead_enriched(session, lead_id, public_id, quiet=quiet):
            return False
        profile_data = lead_profile_by_id(lead_id)
        if not profile_data:
            return False

    from linkedin.ml.embeddings import embed_profile

    if not quiet:
        logger.warning("Lazy-embedded %s (lead_id=%d) — should already have been embedded at discovery", public_id, lead_id)
    return embed_profile(lead_id, public_id, profile_data)


def load_embedding(lead_id: int, public_id: str, session):
    """Load embedding array, lazily enriching+embedding if needed.

    The embedding should already exist from eager discovery. The lazy
    fallback is kept for robustness and should rarely fire in practice.
    """
    from crm.models import Lead

    ensure_profile_embedded(lead_id, public_id, session)
    lead = Lead.objects.filter(pk=lead_id).only("embedding").first()
    return lead.embedding_array if lead else None


def _fetch_profile(session, public_id: str) -> dict | None:
    """Call Voyager API for a single profile. Returns parsed profile or None."""
    from linkedin.api.client import PlaywrightLinkedinAPI

    session.ensure_browser()
    api = PlaywrightLinkedinAPI(session=session)
    try:
        profile, _raw = api.get_profile(public_identifier=public_id)
        return profile
    except AuthenticationError:
        raise
    except (IOError, ValueError) as exc:
        logger.warning("Voyager API failed for %s: %s", public_id, exc)
        return None
