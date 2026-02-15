# linkedin/lanes/enrich.py
from __future__ import annotations

import logging

from django.db import transaction

from linkedin.api.client import PlaywrightLinkedinAPI
from linkedin.conf import CAMPAIGN_CONFIG
from linkedin.db.crm_profiles import (
    count_pending_scrape,
    get_next_url_to_scrape,
    save_scraped_profile,
    set_profile_state,
    url_to_public_id,
    public_id_to_url,
)
from linkedin.navigation.enums import ProfileState

logger = logging.getLogger(__name__)


def is_preexisting_connection(profile: dict) -> bool:
    """Check if a profile is a pre-existing connection (not initiated by automation).

    Returns False when follow_up_existing_connections is enabled (all connections
    are treated as automation targets). Otherwise returns True when
    connection_degree == 1.
    """
    if CAMPAIGN_CONFIG["follow_up_existing_connections"]:
        return False
    return profile.get("connection_degree") == 1


class EnrichLane:
    def __init__(self, session):
        self.session = session

    def can_execute(self) -> bool:
        return count_pending_scrape(self.session) > 0

    def execute(self):
        urls = get_next_url_to_scrape(self.session)

        if not urls:
            return

        self.session.ensure_browser()
        api = PlaywrightLinkedinAPI(session=self.session)

        url = urls[0]
        try:
            profile, data = api.get_profile(profile_url=url)
            public_id = url_to_public_id(url)

            if not profile:
                set_profile_state(self.session, public_id, ProfileState.FAILED.value)
                return

            with transaction.atomic():
                save_scraped_profile(self.session, url, profile, data)

                if is_preexisting_connection(profile):
                    set_profile_state(
                        self.session, public_id, ProfileState.IGNORED.value,
                        reason="Pre-existing connection (not initiated by automation)",
                    )
                else:
                    set_profile_state(self.session, public_id, ProfileState.ENRICHED.value)
                    self._embed_profile(public_id, profile)
        except Exception:
            logger.exception("Failed to enrich %s", url)
            try:
                public_id = url_to_public_id(url)
                set_profile_state(self.session, public_id, ProfileState.FAILED.value)
            except Exception:
                pass

    def _embed_profile(self, public_id: str, profile: dict):
        """Compute and store embedding for a freshly enriched profile."""
        from crm.models import Lead

        from linkedin.ml.embeddings import embed_profile

        clean_url = public_id_to_url(public_id)
        lead = Lead.objects.filter(website=clean_url).first()
        if not lead:
            return

        if embed_profile(lead.pk, public_id, profile):
            logger.debug("Embedded %s during enrichment", public_id)
