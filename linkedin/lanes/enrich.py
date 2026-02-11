# linkedin/lanes/enrich.py
from __future__ import annotations

import logging

from linkedin.api.client import PlaywrightLinkedinAPI
from linkedin.db.crm_profiles import (
    count_pending_scrape,
    get_next_url_to_scrape,
    save_scraped_profile,
    set_profile_state,
    url_to_public_id,
)
from linkedin.navigation.enums import ProfileState
from linkedin.navigation.throttle import ThrottleState

logger = logging.getLogger(__name__)


class EnrichLane:
    def __init__(self, session):
        self.session = session
        self._throttle = ThrottleState()

    def can_execute(self) -> bool:
        return count_pending_scrape(self.session) > 0

    def execute(self):
        batch_size = self._throttle.determine_batch_size(self.session)
        urls = get_next_url_to_scrape(self.session, limit=batch_size)

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
            except Exception:
                logger.exception("Failed to enrich %s", url)
                try:
                    public_id = url_to_public_id(url)
                    set_profile_state(self.session, public_id, ProfileState.FAILED.value)
                except Exception:
                    pass
