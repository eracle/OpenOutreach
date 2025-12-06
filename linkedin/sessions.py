# linkedin/sessions.py
from __future__ import annotations

import logging
from pathlib import Path  # noqa
from typing import NamedTuple, Optional

from linkedin.conf import get_account_config
from linkedin.csv_launcher import hash_file
from linkedin.db.engine import Database
from linkedin.navigation.login import init_playwright_session

logger = logging.getLogger(__name__)


class SessionKey(NamedTuple):
    handle: str
    campaign_name: str
    csv_hash: str

    def __str__(self) -> str:
        return f"{self.handle}::{self.campaign_name}::{self.csv_hash}"

    @classmethod
    def make(cls, handle: str, campaign_name: str, csv_path: Path | str) -> "SessionKey":
        csv_hash = hash_file(csv_path)
        return cls(handle=handle, campaign_name=campaign_name, csv_hash=csv_hash)

    def as_filename_safe(self) -> str:
        return f"{self.handle}--{self.campaign_name}--{self.csv_hash}"


class AccountSessionRegistry:
    _instances: dict[SessionKey, "AccountSession"] = {}

    @classmethod
    def get_or_create(
            cls,
            handle: str,
            campaign_name: str,
            csv_hash: str,
    ) -> "AccountSession":
        key = SessionKey(handle, campaign_name, csv_hash)

        if key not in cls._instances:
            cls._instances[key] = AccountSession(key)
            logger.info("Created new account session → %s", key)
        else:
            logger.debug("Reusing existing account session → %s", key)

        return cls._instances[key]

    @classmethod
    def get_or_create_from_path(
            cls,
            handle: str,
            campaign_name: str,
            csv_path: Path | str,
    ) -> "AccountSession":
        csv_path = Path(csv_path)
        key = SessionKey.make(handle, campaign_name, csv_path)
        return cls.get_or_create(key.handle, key.campaign_name, key.csv_hash)

    @classmethod
    def get_existing(cls, key: SessionKey) -> Optional["AccountSession"]:
        return cls._instances.get(key)

    @classmethod
    def clear_all(cls):
        for session in list(cls._instances.values()):
            session.close()
        cls._instances.clear()


class AccountSession:
    def __init__(self, key: SessionKey):
        self.key = key
        self.handle = key.handle
        self.campaign_name = key.campaign_name
        self.csv_hash = key.csv_hash

        self.account_cfg = get_account_config(self.handle)
        self.db = Database.from_handle(self.handle)

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
                handle=self.handle
            )

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


# ——————————————————————————————————————————————————————————————
if __name__ == "__main__":
    import logging
    from pathlib import Path

    logging.getLogger().handlers.clear()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s │ %(levelname)-8s │ %(message)s",
        datefmt="%H:%M:%S",
    )

    import sys

    if len(sys.argv) != 2:
        print("Usage: python -m linkedin.sessions <handle>")
        sys.exit(1)

    handle = sys.argv[1]

    CAMPAIGN_NAME = "connect_follow_up"
    INPUT_CSV_PATH = Path("./assets/inputs/urls.csv")

    session = AccountSessionRegistry.get_or_create_from_path(
        handle=handle,
        campaign_name=CAMPAIGN_NAME,
        csv_path=INPUT_CSV_PATH,
    )

    session.ensure_browser()  # ← this does everything

    print("\nSession ready! Use session.page, session.context, etc.")
    print(f"   Handle   : {session.handle}")
    print(f"   Campaign : {session.campaign_name}")
    print(f"   CSV hash : {session.csv_hash}")
    print(f"   Key      : {session.key}")
    print("   Browser survives crash/reboot/Ctrl+C\n")

    session.page.pause()  # keeps browser open for manual testing
