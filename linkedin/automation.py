# linkedin/automation.py
"""
Singleton per (handle + campaign + csv_hash).
Holds shared browser, DB, and output file.
All campaign steps call methods here → late binding → 100% pickle-safe.
"""

import csv
import logging
from pathlib import Path
from typing import Optional, Dict, Any

from linkedin.actions.connect import send_connection_request
from linkedin.actions.message import send_follow_up_message
# New-style actions – they all accept LinkedInAutomation as first argument
from linkedin.actions.profile import enrich_profile
from linkedin.actions.search import search_profile
from linkedin.conf import get_account_config
from linkedin.db.engine import Database
from linkedin.navigation.enums import ConnectionStatus
from linkedin.navigation.login import get_resources_with_state_management, PlaywrightResources

logger = logging.getLogger(__name__)


# ======================================================================
# Registry – one singleton per running campaign
# ======================================================================
class AutomationRegistry:
    _instances: dict[str, "LinkedInAutomation"] = {}

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
    ) -> "LinkedInAutomation":
        k = cls.key(handle, campaign_name, csv_hash)
        if k not in cls._instances:
            cls._instances[k] = LinkedInAutomation(
                handle=handle,
                campaign_name=campaign_name,
                csv_hash=csv_hash,
                input_csv=input_csv,
            )
            logger.info(f"Created new automation singleton → {k}")
        else:
            logger.debug(f"Reusing existing automation singleton → {k}")
        return cls._instances[k]

    @classmethod
    def clear_all(cls):
        for inst in list(cls._instances.values()):
            inst.close()
        cls._instances.clear()


# ======================================================================
# The actual singleton – one per campaign run
# ======================================================================
class LinkedInAutomation:
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
        return AutomationRegistry.key(self.handle, self.campaign_name, self.csv_hash)

    # ------------------------------------------------------------------
    # Auto-recovering browser – survives crashes and process restarts
    # ------------------------------------------------------------------
    @property
    def browser(self) -> PlaywrightResources:
        if self._resources is None or self._resources.page.is_closed():
            logger.info("Launching / recovering browser session")
            self._resources = get_resources_with_state_management(handle=self.handle)
        return self._resources

    # ------------------------------------------------------------------
    # Public action methods – called directly from workflow engine
    # ------------------------------------------------------------------
    def enrich_profile(self, profile: Dict[str, Any]) -> Dict[str, Any]:
        """Scrape additional data from the profile page."""
        return enrich_profile(self, profile)

    def send_connection_request(
            self,
            profile: Dict[str, Any],
            *,
            template_file: Optional[str] = None,
            template_type: str = "jinja",
    ) -> None:
        """
        Send a connection request (with optional personalized note).
        """
        send_connection_request(
            automation=self,
            profile=profile,
            template_file=template_file,
            template_type=template_type,
        )

    def is_connection_accepted(self, profile: Dict[str, Any]) -> bool:
        """Check if we are now 1st-degree connected."""
        from linkedin.actions.connections import get_connection_status
        return get_connection_status(self, profile) == ConnectionStatus.CONNECTED

    def send_follow_up_message(
            self,
            profile: Dict[str, Any],
            *,
            template_file: Optional[str] = None,
            template_type: str = "jinja",
            message: Optional[str] = None,
    ) -> None:
        """Send a message to a 1st-degree connection."""
        send_follow_up_message(
            automation=self,
            profile=profile,
            template_file=template_file,
            template_type=template_type,
            message=message,
        )

    def navigate_to_profile(self, profile: Dict[str, Any]) -> None:
        """Utility – go directly to a profile (used by multiple actions)."""
        search_profile(self, profile)


    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    def close(self):
        if self._resources:
            try:
                self._resources.context.close()
                self._resources.browser.close()
                logger.info("Browser closed gracefully")
            except Exception as e:
                logger.debug(f"Error closing browser: {e}")
            finally:
                self._resources = None
        self.db.close()
        logger.info(f"Automation instance closed → {self.key()}")

    def __del__(self):
        # Fallback cleanup if someone forgets to call close()
        try:
            self.close()
        except:
            pass
