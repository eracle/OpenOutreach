# linkedin/lanes/enrich.py
from __future__ import annotations

import logging

from linkedin.api.client import PlaywrightLinkedinAPI
from linkedin.conf import CAMPAIGN_CONFIG
from linkedin.sessions.account import human_delay
from linkedin.db.crm_profiles import (
    count_pending_scrape,
    get_next_url_to_scrape,
    save_scraped_profile,
    set_profile_state,
    url_to_public_id,
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

        for url in urls:
            try:
                profile, data = api.get_profile(profile_url=url)
                save_scraped_profile(self.session, url, profile, data)
                if not profile:
                    public_id = url_to_public_id(url)
                    set_profile_state(self.session, public_id, ProfileState.FAILED.value)
                    continue

                if is_preexisting_connection(profile):
                    public_id = url_to_public_id(url)
                    set_profile_state(
                        self.session, public_id, ProfileState.IGNORED.value,
                        reason="Pre-existing connection (not initiated by automation)",
                    )

                human_delay(0.5, 1.5)
            except Exception:
                logger.exception("Failed to enrich %s", url)
                try:
                    public_id = url_to_public_id(url)
                    set_profile_state(self.session, public_id, ProfileState.FAILED.value)
                except Exception:
                    pass
