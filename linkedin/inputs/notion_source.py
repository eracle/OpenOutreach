# linkedin/inputs/notion_source.py
"""
Load profiles from Notion database as input source.

Queries Notion for profiles that need processing (State is empty or "new").
"""
import logging
from typing import List, Dict, Any, Optional

import requests

from linkedin.conf import NOTION_API_KEY, NOTION_DATABASE_ID, is_notion_enabled
from linkedin.db.profiles import url_to_public_id

logger = logging.getLogger(__name__)

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"  # Stable version


def _extract_url_from_page(page: Dict[str, Any]) -> Optional[str]:
    """Extract LinkedIn URL from a Notion page."""
    props = page.get("properties", {})

    # Try "LinkedIn URL" property first
    url_prop = props.get("LinkedIn URL", {})
    if url_prop.get("url"):
        return url_prop["url"]

    # Try "URL" property as fallback
    url_prop = props.get("URL", {})
    if url_prop.get("url"):
        return url_prop["url"]

    return None


def _extract_state_from_page(page: Dict[str, Any]) -> Optional[str]:
    """Extract State from a Notion page."""
    props = page.get("properties", {})
    state_prop = props.get("State", {})

    if state_prop.get("select"):
        return state_prop["select"].get("name")

    return None


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


def load_profiles_from_notion() -> List[Dict[str, Any]]:
    """
    Query Notion for profiles to process.

    Returns profiles where:
    - State is empty/null, OR
    - State is "new" (user-added)
    - State is "discovered" (to continue processing)

    Returns:
        List of profile dicts with 'url' and 'public_identifier' keys
        (same format as load_profiles_df returns)
    """
    if not is_notion_enabled():
        logger.warning("Notion is not enabled, cannot load profiles from Notion")
        return []

    try:
        # Query for profiles that need processing
        response = _notion_request(
            "POST",
            f"/databases/{NOTION_DATABASE_ID}/query",
            {
                "filter": {
                    "or": [
                        # State is empty (not set)
                        {
                            "property": "State",
                            "select": {
                                "is_empty": True
                            }
                        },
                        # State is "new" (user just added)
                        {
                            "property": "State",
                            "select": {
                                "equals": "new"
                            }
                        },
                        # State is "discovered" (needs enrichment)
                        {
                            "property": "State",
                            "select": {
                                "equals": "discovered"
                            }
                        },
                        # State is "enriched" (needs connection request)
                        {
                            "property": "State",
                            "select": {
                                "equals": "enriched"
                            }
                        },
                        # State is "pending" (waiting for connection acceptance)
                        {
                            "property": "State",
                            "select": {
                                "equals": "pending"
                            }
                        },
                        # State is "connected" (needs follow-up message)
                        {
                            "property": "State",
                            "select": {
                                "equals": "connected"
                            }
                        },
                    ]
                }
            }
        )

        profiles = []
        for page in response.get("results", []):
            url = _extract_url_from_page(page)
            if not url:
                logger.warning("Notion page %s has no LinkedIn URL, skipping", page.get("id"))
                continue

            public_id = url_to_public_id(url)
            if not public_id:
                logger.warning("Could not extract public_id from URL: %s", url)
                continue

            profiles.append({
                "url": url,
                "public_identifier": public_id,
                "notion_page_id": page.get("id"),  # Store for later updates
            })

        logger.info("Loaded %d profiles from Notion database", len(profiles))
        return profiles

    except requests.exceptions.HTTPError as e:
        logger.error("Notion API error loading profiles: %s", e)
        return []
    except Exception as e:
        logger.error("Failed to load profiles from Notion: %s", e)
        return []


def get_notion_page_id_by_public_id(public_identifier: str) -> Optional[str]:
    """
    Find the Notion page ID for a given public_identifier.

    Useful for updating a specific profile's Notion page.
    """
    if not is_notion_enabled():
        return None

    try:
        response = _notion_request(
            "POST",
            f"/databases/{NOTION_DATABASE_ID}/query",
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
            return results[0]["id"]

        return None

    except Exception as e:
        logger.error("Failed to find Notion page for %s: %s", public_identifier, e)
        return None
