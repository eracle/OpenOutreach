# linkedin/api/notion_sync.py
"""
Notion database sync implementation.

Uses direct HTTP requests to the Notion API.
Supports both creating new pages and updating existing ones.
"""
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

import requests

from linkedin.conf import NOTION_API_KEY, NOTION_DATABASE_ID, is_notion_enabled
from linkedin.api.notion_field_mapper import profile_to_notion_properties

logger = logging.getLogger(__name__)

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def _notion_request(method: str, endpoint: str, json_data: Dict = None) -> Dict:
    """Make a request to the Notion API."""
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION,
    }
    url = f"{NOTION_API_BASE}{endpoint}"

    response = requests.request(method, url, headers=headers, json=json_data)
    response.raise_for_status()
    return response.json()


class NotionSyncClient:
    """Client for syncing LinkedIn profiles to Notion database."""

    def __init__(self):
        if not is_notion_enabled():
            raise RuntimeError("Notion sync is not enabled or configured")

        self.database_id = NOTION_DATABASE_ID
        self._page_cache: Dict[str, str] = {}  # public_id -> page_id

    def _find_existing_page(self, public_identifier: str) -> Optional[str]:
        """
        Find existing Notion page by LinkedIn public_identifier.

        Returns page_id if found, None otherwise.
        """
        # Check cache first
        if public_identifier in self._page_cache:
            return self._page_cache[public_identifier]

        try:
            response = _notion_request(
                "POST",
                f"/databases/{self.database_id}/query",
                {
                    "filter": {
                        "property": "Public ID",
                        "rich_text": {
                            "equals": public_identifier
                        }
                    }
                }
            )

            results = response.get("results", [])
            if results:
                page_id = results[0]["id"]
                self._page_cache[public_identifier] = page_id
                return page_id

            return None

        except requests.exceptions.HTTPError as e:
            logger.warning("Failed to query Notion for existing page: %s", e)
            return None

    def sync_profile(
        self,
        profile: Dict[str, Any],
        state: Optional[str] = None,
        created_at: Optional[datetime] = None,
        updated_at: Optional[datetime] = None,
        message_sent: Optional[str] = None,
        notion_page_id: Optional[str] = None,
    ) -> bool:
        """
        Sync a single profile to Notion.

        Creates new page or updates existing one based on public_identifier.

        Args:
            profile: The profile JSON from the Profile model
            state: Current profile state
            created_at: When profile was first discovered
            updated_at: When profile was last updated
            message_sent: Follow-up message content if sent
            notion_page_id: Direct Notion page ID (for profiles loaded from Notion)

        Returns:
            True if sync succeeded, False otherwise
        """
        if not profile:
            logger.warning("Empty profile data, skipping sync")
            return False

        public_id = profile.get("public_identifier")
        if not public_id:
            logger.warning("Profile missing public_identifier, skipping sync")
            return False

        # Debug: log what we're syncing
        logger.debug("Syncing profile %s with fields: full_name=%s, headline=%s, state=%s",
                     public_id, profile.get("full_name"), profile.get("headline"), state)

        try:
            properties = profile_to_notion_properties(
                profile=profile,
                state=state,
                created_at=created_at,
                updated_at=updated_at,
                message_sent=message_sent,
            )

            # Use direct page ID if available, otherwise search by Public ID
            existing_page_id = notion_page_id or self._find_existing_page(public_id)

            if existing_page_id:
                # Update existing page
                _notion_request(
                    "PATCH",
                    f"/pages/{existing_page_id}",
                    {"properties": properties}
                )
                logger.debug("Updated Notion page for %s (page_id=%s)", public_id, existing_page_id)
            else:
                # Create new page
                response = _notion_request(
                    "POST",
                    "/pages",
                    {
                        "parent": {"database_id": self.database_id},
                        "properties": properties
                    }
                )
                self._page_cache[public_id] = response["id"]
                logger.debug("Created Notion page for %s", public_id)

            return True

        except requests.exceptions.HTTPError as e:
            logger.error("Notion API error syncing %s: %s", public_id, e)
            return False
        except Exception as e:
            logger.error("Unexpected error syncing %s to Notion: %s", public_id, e)
            return False

    def sync_profile_row(self, profile_row) -> bool:
        """
        Sync a Profile model instance to Notion.

        Extracts all fields from the SQLAlchemy model.

        Args:
            profile_row: Profile model instance from database

        Returns:
            True if sync succeeded, False otherwise
        """
        if not profile_row or not profile_row.profile:
            return False

        return self.sync_profile(
            profile=profile_row.profile,
            state=profile_row.state,
            created_at=profile_row.created_at,
            updated_at=profile_row.updated_at,
            message_sent=profile_row.message_sent if hasattr(profile_row, 'message_sent') else None,
            notion_page_id=profile_row.notion_page_id if hasattr(profile_row, 'notion_page_id') else None,
        )


def sync_to_notion(profile_rows: List) -> bool:
    """
    Main entry point for Notion sync.

    Args:
        profile_rows: List of Profile model instances

    Returns:
        True if all profiles synced successfully, False if any failed
    """
    if not is_notion_enabled():
        logger.debug("Notion sync disabled, skipping")
        return True

    if not profile_rows:
        logger.debug("No profiles to sync")
        return True

    try:
        client = NotionSyncClient()
        success_count = 0
        failure_count = 0

        for row in profile_rows:
            if client.sync_profile_row(row):
                success_count += 1
            else:
                failure_count += 1

        if failure_count > 0:
            logger.warning("Notion sync: %d succeeded, %d failed", success_count, failure_count)
            return False

        logger.info("Notion sync: %d profile(s) synced successfully", success_count)
        return True

    except Exception as e:
        logger.error("Failed to initialize Notion sync: %s", e)
        return False


def sync_single_profile(
    profile: Dict[str, Any],
    state: str,
    created_at: Optional[datetime] = None,
    updated_at: Optional[datetime] = None,
    message_sent: Optional[str] = None,
) -> bool:
    """
    Sync a single profile immediately (for real-time updates).

    Called directly after state changes instead of waiting for DB close.
    """
    if not is_notion_enabled():
        return True

    try:
        client = NotionSyncClient()
        return client.sync_profile(
            profile=profile,
            state=state,
            created_at=created_at,
            updated_at=updated_at,
            message_sent=message_sent,
        )
    except Exception as e:
        logger.error("Failed to sync profile to Notion: %s", e)
        return False
