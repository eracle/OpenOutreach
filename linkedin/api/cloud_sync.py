# linkedin/api/cloud_sync.py
"""
Cloud sync orchestration.

Handles syncing profiles to external services (Notion, etc.)
Called from Database.close() for batch sync of unsynced profiles.
"""
import logging
from typing import List

from linkedin.conf import is_notion_enabled

logger = logging.getLogger(__name__)


def sync_profiles(profile_rows: List) -> bool:
    """
    Sync profiles to configured cloud services.

    Args:
        profile_rows: List of Profile model instances (full SQLAlchemy rows)

    Returns:
        True if sync succeeded (or no sync configured), False on failure
    """
    if not profile_rows:
        return True

    # Notion sync
    if is_notion_enabled():
        from linkedin.api.notion_sync import sync_to_notion

        logger.info("Syncing %d profiles to Notion...", len(profile_rows))
        if not sync_to_notion(profile_rows):
            logger.error("Notion sync failed")
            return False
    else:
        logger.debug("No cloud sync configured, profiles marked as synced")

    return True
