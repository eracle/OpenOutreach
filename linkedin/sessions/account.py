# linkedin/sessions/account.py
from __future__ import annotations

import json
import logging
import random
import time
from pathlib import Path

from linkedin.conf import get_account_config, MIN_DELAY, MAX_DELAY
from linkedin.navigation.login import init_playwright_session

logger = logging.getLogger(__name__)

# The main LinkedIn auth cookie
_AUTH_COOKIE_NAME = "li_at"


def human_delay(min_val, max_val):
    delay = random.uniform(min_val, max_val)
    logger.debug(f"Pause: {delay:.2f}s")
    time.sleep(delay)


class AccountSession:
    def __init__(self, handle: str):
        from django.contrib.auth.models import User

        self.handle = handle.strip().lower()

        self.account_cfg = get_account_config(self.handle)

        # Look up or create the Django User for this handle
        self.django_user, created = User.objects.get_or_create(
            username=self.handle,
            defaults={"is_staff": True, "is_active": True},
        )
        if created:
            self.django_user.set_unusable_password()
            self.django_user.save()
            logger.info("Auto-created Django user for %s", self.handle)

        # Playwright objects – created on first access or after crash
        self.page = None
        self.context = None
        self.browser = None
        self.playwright = None

    def ensure_browser(self):
        """Launch or recover browser + login if needed. Call before using .page"""
        if not self.page or self.page.is_closed():
            logger.debug("Launching/recovering browser for %s", self.handle)
            init_playwright_session(session=self, handle=self.handle)
        else:
            self._maybe_refresh_cookies()

    def wait(self, min_delay=MIN_DELAY, max_delay=MAX_DELAY):
        human_delay(min_delay, max_delay)
        self.page.wait_for_load_state("load")

    def _maybe_refresh_cookies(self):
        """Re-login if the li_at auth cookie in the saved file is expired."""
        cookie_file = Path(self.account_cfg["cookie_file"])
        if not cookie_file.exists():
            return
        try:
            data = json.loads(cookie_file.read_text())
        except (json.JSONDecodeError, OSError):
            return
        for cookie in data.get("cookies", []):
            if cookie.get("name") == _AUTH_COOKIE_NAME:
                expires = cookie.get("expires", -1)
                if expires > 0 and expires < time.time():
                    logger.warning("Auth cookie expired for %s — re-authenticating", self.handle)
                    self.close()
                    init_playwright_session(session=self, handle=self.handle)
                return

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

        logger.info("Account session closed → %s", self.handle)

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def __repr__(self) -> str:
        return f"<AccountSession {self.handle}>"
