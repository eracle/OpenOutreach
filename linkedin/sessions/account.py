# linkedin/sessions/account.py
from __future__ import annotations

import logging
import random
import time

from linkedin.actions.profile import PlaywrightLinkedinAPI
from linkedin.conf import get_account_config, MIN_DELAY, MAX_DELAY, OPPORTUNISTIC_SCRAPING
from linkedin.navigation.login import init_playwright_session
from linkedin.navigation.throttle import determine_batch_size

logger = logging.getLogger(__name__)

MIN_API_DELAY = 0.250
MAX_API_DELAY = 0.500


def human_delay(min_val, max_val):
    delay = random.uniform(min_val, max_val)
    logger.debug(f"Pause: {delay:.2f}s")
    time.sleep(delay)


class AccountSession:
    def __init__(self, handle: str):
        from linkedin.db.engine import Database

        self.handle = handle.strip().lower()

        self.account_cfg = get_account_config(self.handle)
        self.db = Database.from_handle(self.handle)
        self.db_session = self.db.get_session()  # long-lived session per account

        # Playwright objects – created on first access or after crash
        self.page = None
        self.context = None
        self.browser = None
        self.playwright = None

    def ensure_browser(self):
        """Launch or recover browser + login if needed. Call before using .page"""
        if not self.page or self.page.is_closed():
            logger.info("Launching/recovering browser for %s", self.handle)
            init_playwright_session(session=self, handle=self.handle)

    def wait(self, min_delay=MIN_DELAY, max_delay=MAX_DELAY, to_scrape=OPPORTUNISTIC_SCRAPING):
        if not to_scrape:
            human_delay(min_delay, max_delay)
            self.page.wait_for_load_state("load")
            return

        from linkedin.db.profiles import get_next_url_to_scrape

        logger.debug(f"Pausing: {MAX_DELAY}s")
        amount_to_scrape = determine_batch_size(self)

        urls = get_next_url_to_scrape(self, limit=amount_to_scrape)
        if not urls:
            human_delay(min_delay, max_delay)
            self.page.wait_for_load_state("load")
            return

        from linkedin.db.profiles import save_scraped_profile
        min_api_delay = max(min_delay / len(urls), MIN_API_DELAY)
        max_api_delay = max(max_delay / len(urls), MAX_API_DELAY)
        api = PlaywrightLinkedinAPI(session=self)

        for url in urls:
            human_delay(min_api_delay, max_api_delay)
            profile, data = api.get_profile(profile_url=url)
            save_scraped_profile(self, url, profile, data)
            logger.debug(f"Auto-scraped → {profile.get('full_name')} – {url}") if profile else None

    def close(self):
        if self.context:
            try:
                self.context.close()
                if self.browser:
                    self.browser.close()
                if self.playwright:
                    self.playwright.stop()
                logger.info("Browser closed gracefully (%s)", self.handle)
            except Exception as e:
                logger.debug("Error closing browser: %s", e)
            finally:
                self.page = self.context = self.browser = self.playwright = None

        self.db.close()
        logger.info("Account session closed → %s", self.handle)

    def __del__(self):
        try:
            self.close()
        except:
            pass

    def __repr__(self) -> str:
        return f"<AccountSession {self.handle}>"
