# linkedin/api/logging.py
import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


def log_profiles(get_profile_json: List[Dict[str, Any]]):
    logger.debug(f"Logging {len(get_profile_json)} profiles.")
    return True
