# linkedin/account_session.py
"""
Singleton AccountSession per (handle + campaign_name + csv_hash).

Provides:
- One browser + DB instance per unique campaign run on a given account
- Automatic recovery after crashes/reboots/Ctrl+C
- A clean, reusable SessionKey that can be built anywhere from the 3 inputs
"""

from __future__ import annotations

import logging
from pathlib import Path  # noqa
from typing import NamedTuple, Optional

from linkedin.conf import get_account_config
from linkedin.csv_launcher import hash_file
from linkedin.db.engine import Database
from linkedin.navigation.login import get_resources_with_state_management, PlaywrightResources

logger = logging.getLogger(__name__)


# ======================================================================
# Deterministic, hashable, human-readable session key
# ======================================================================
class SessionKey(NamedTuple):
    handle: str
    campaign_name: str
    csv_hash: str

    def __str__(self) -> str:
        """Human-readable representation for logs and filenames."""
        return f"{self.handle}::{self.campaign_name}::{self.csv_hash}"

    @classmethod
    def make(cls, handle: str, campaign_name: str, csv_path: Path | str) -> "SessionKey":
        """
        Convenience factory: compute CSV hash automatically.
        Use this in 99% of cases.
        """
        csv_hash = hash_file(csv_path)
        return cls(handle=handle, campaign_name=campaign_name, csv_hash=csv_hash)

    def as_filename_safe(self) -> str:
        """For saving files like state_eracle--connect_follow_up--a1b2c3.json"""
        return f"{self.handle}--{self.campaign_name}--{self.csv_hash}"


# ======================================================================
# Registry – one singleton per (handle, campaign, csv_hash)
# ======================================================================
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
        """
        Most convenient entry point – just give the CSV file path.
        """
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


# ======================================================================
# The actual singleton session
# ======================================================================
class AccountSession:
    def __init__(self, key: SessionKey):
        self.key = key
        self.handle = key.handle
        self.campaign_name = key.campaign_name
        self.csv_hash = key.csv_hash

        self.account_cfg = get_account_config(self.handle)
        self._resources: Optional[PlaywrightResources] = None
        self.db = Database.from_handle(self.handle)

    # ------------------------------------------------------------------
    # Auto-recovering browser session
    # ------------------------------------------------------------------
    @property
    def resources(self) -> PlaywrightResources:
        if self._resources is None or self._resources.page.is_closed():
            logger.info("Launching/recovering browser for account '%s' – campaign '%s'",
                        self.handle, self.campaign_name)
            self._resources = get_resources_with_state_management(handle=self.handle)
        return self._resources

    # ------------------------------------------------------------------
    # Graceful cleanup
    # ------------------------------------------------------------------
    def close(self):
        if self._resources:
            try:
                self._resources.context.close()
                self._resources.browser.close()
                logger.info("Browser closed gracefully (%s)", self.handle)
            except Exception as e:
                logger.debug("Error closing browser for %s: %s", self.handle, e)
            finally:
                self._resources = None

        self.db.close()
        logger.info("Account session closed → %s", self.key)

    def __del__(self):
        # Safety net
        try:
            self.close()
        except:
            pass

    def __repr__(self) -> str:
        return f"<AccountSession {self.key}>"


# ——————————————————————————————————————————————————————————————
# CLI – Quick test / persistent runner
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

    # ———————————— CONFIGURE HERE ————————————
    HANDLE = "eracle"
    CAMPAIGN_NAME = "connect_follow_up"
    INPUT_CSV_PATH = Path("./assets/inputs/urls.csv")
    # ————————————————————————————————————————

    session = AccountSessionRegistry.get_or_create_from_path(
        handle=HANDLE,
        campaign_name=CAMPAIGN_NAME,
        csv_path=INPUT_CSV_PATH,
    )

    print("\nLinkedIn Account Session STARTED & PERSISTENT")
    print(f"   Account       : {session.handle}")
    print(f"   Campaign      : {session.campaign_name}")
    print(f"   CSV hash      : {session.csv_hash}")
    print(f"   Session key   : {session.key}")
    print(f"   DB path       : {session.db.db_path}")
    print("   Browser & DB survive crashes, reboots, Ctrl+C")
    print("   Use SessionKey.make(...) anywhere to get the same session!\n")
