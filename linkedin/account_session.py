# linkedin/account_session.py
"""
Singleton per (handle + campaign + csv_hash).
Represents one active LinkedIn account session during a campaign run.
Owns the browser instance, database connection, and campaign state.
"""

import logging
from pathlib import Path
from typing import Optional

from linkedin.conf import get_account_config
from linkedin.db.engine import Database
from linkedin.navigation.login import get_resources_with_state_management, PlaywrightResources

logger = logging.getLogger(__name__)


# ======================================================================
# Registry – one singleton per running campaign on a given account
# ======================================================================
class AccountSessionRegistry:
    _instances: dict[str, "AccountSession"] = {}

    @classmethod
    def key(cls, handle: str, campaign_name: str, csv_hash: str) -> str:
        return f"{handle}::{campaign_name}::{csv_hash}"

    @classmethod
    def get_or_create(
            cls,
            handle: str,
            campaign_name: str,
            csv_hash: str,
            input_csv: Path,
    ) -> "AccountSession":
        k = cls.key(handle, campaign_name, csv_hash)
        if k not in cls._instances:
            cls._instances[k] = AccountSession(
                handle=handle,
                campaign_name=campaign_name,
                csv_hash=csv_hash,
                input_csv=input_csv,
            )
            logger.info(f"Created new account session → {k}")
        else:
            logger.debug(f"Reusing existing account session → {k}")
        return cls._instances[k]

    @classmethod
    def clear_all(cls):
        for session in list(cls._instances.values()):
            session.close()
        cls._instances.clear()


# ======================================================================
# The actual singleton – one per campaign run on a specific account
# ======================================================================
class AccountSession:
    def __init__(
            self,
            handle: str,
            campaign_name: str,
            csv_hash: str,
            input_csv: Path,
    ):
        self.handle = handle
        self.campaign_name = campaign_name
        self.csv_hash = csv_hash
        self.input_csv = input_csv

        self.account_cfg = get_account_config(handle)
        self._resources: Optional[PlaywrightResources] = None
        self.db = Database.from_handle(handle)

    def key(self) -> str:
        return AccountSessionRegistry.key(self.handle, self.campaign_name, self.csv_hash)

    # ------------------------------------------------------------------
    # Auto-recovering browser session
    # ------------------------------------------------------------------
    @property
    def browser(self) -> PlaywrightResources:
        if self._resources is None or self._resources.page.is_closed():
            logger.info("Launching / recovering browser session for %s", self.handle)
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
                logger.debug(f"Error closing browser for {self.handle}: {e}")
            finally:
                self._resources = None
        self.db.close()
        logger.info(f"Account session closed → {self.key()}")

    def __del__(self):
        # Safety net
        try:
            self.close()
        except:
            pass


# ——————————————————————————————————————————————————————————————
# CLI – Persistent AccountSession runner
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

    # ——————————————————————————————————
    # Configuration (change these values)
    # ——————————————————————————————————
    HANDLE = "eracle"  # LinkedIn account handle
    CAMPAIGN_NAME = "linked_in_connect_follow_up"  # Your campaign identifier
    INPUT_CSV_PATH = Path("./assets/inputs/urls.csv")  # Path to your input CSV

    # ——————————————————————————————————
    # Create / reuse the singleton session
    # ——————————————————————————————————

    # Compute a stable hash of the CSV content so the same file → same session
    csv_hash = Database.hash_file(INPUT_CSV_PATH)  # assuming Database has this helper
    # If your Database class doesn't have it yet, you can use:
    # csv_hash = hashlib.sha256(INPUT_CSV_PATH.read_bytes()).hexdigest()[:12]

    session = AccountSessionRegistry.get_or_create(
        handle=HANDLE,
        campaign_name=CAMPAIGN_NAME,
        csv_hash=csv_hash,
        input_csv=INPUT_CSV_PATH,
    )

    print("\nLinkedIn Account Session STARTED & PERSISTENT")
    print(f"   Account       : {session.handle}")
    print(f"   Campaign      : {session.campaign_name}")
    print(f"   Input CSV     : {session.input_csv}")
    print(f"   CSV hash      : {session.csv_hash}")
    print(f"   Session key   : {session.key()}")
    print(f"   DB path       : {session.db.db_path}")
    print("   Browser & DB survive crashes, reboots, Ctrl+C\n")
