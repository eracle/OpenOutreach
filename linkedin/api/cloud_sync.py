# linkedin/api/logging.py
import logging

logger = logging.getLogger(__name__)


def sync_profiles(data: list[dict]):
    logger.debug(f"Logging {len(data)} profiles.")
    return True
