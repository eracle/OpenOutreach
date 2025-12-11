# linkedin/sessions/account.py
from __future__ import annotations

import logging
import random
import time

from linkedin.actions.profile import PlaywrightLinkedinAPI
from linkedin.conf import get_account_config, SYNC_PROFILES
from linkedin.navigation.login import init_playwright_session
from linkedin.navigation.throttle import get_smooth_scrape_count, _wait_counter
from linkedin.sessions.registry import SessionKey

logger = logging.getLogger(__name__)

MIN_DELAY = 1
MAX_DELAY = 2
MIN_API_DELAY = 0.250
MAX_API_DELAY = 0.500


def human_delay(min, max):
    delay = random.uniform(min, max)
    logger.debug(f"Pause: {delay:.2f}s")
    time.sleep(delay)


class AccountSession:
    def __init__(self, key: "SessionKey"):
        from linkedin.db.engine import Database
        self.key = key
        self.handle = key.handle
        self.campaign_name = key.campaign_name
        self.csv_hash = key.csv_hash

        self.account_cfg = get_account_config(self.handle)
        self.db = Database.from_handle(self.handle)
        self.db_session = self.db.get_session()  # one long-lived session per account run

        # Playwright objects – created on first access or after crash
        self.page = None
        self.context = None
        self.browser = None
        self.playwright = None

    def ensure_browser(self):
        """Launch or recover browser + login if needed. Call before using .page"""
        if not self.page or self.page.is_closed():
            logger.info("Launching/recovering browser for %s – %s", self.handle, self.campaign_name)
            self.page, self.context, self.browser, self.playwright = init_playwright_session(
                session=self,
                handle=self.handle
            )

    def wait(self, min_delay=MIN_DELAY, max_delay=MAX_DELAY):
        from linkedin.db.profiles import count_pending_scrape
        from linkedin.db.profiles import get_next_url_to_scrape
        from linkedin.db.profiles import save_scraped_profile

        if not SYNC_PROFILES:
            human_delay(min_delay, max_delay)
            self.page.wait_for_load_state("load")
            return

        logger.info(f"Pausing: {MAX_DELAY}s")
        pending = count_pending_scrape(self)

        logger.debug(f"Wait #{_wait_counter:04d} | Profiles still needing scrape: {pending}")

        amount_to_scrape = get_smooth_scrape_count(pending)  # keeps original throttling logic happy

        urls = get_next_url_to_scrape(self, limit=amount_to_scrape)
        if urls:
            min_api_delay = max(min_delay / len(urls), MIN_API_DELAY)
            max_api_delay = max(max_delay / len(urls), MAX_API_DELAY)
            api = PlaywrightLinkedinAPI(session=self)

            for url in urls:
                human_delay(min_api_delay, max_api_delay)
                profile, data = api.get_profile(profile_url=url)
                save_scraped_profile(self, url, profile, data)
                logger.debug(f"Auto-scraped → {profile.get('full_name')} – {url}") if profile else None
        else:
            human_delay(min_delay, max_delay)
            self.page.wait_for_load_state("load")

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
        logger.info("Account session closed → %s", self.key)

    def __del__(self):
        try:
            self.close()
        except:
            pass

    def __repr__(self) -> str:
        return f"<AccountSession {self.key}>"
