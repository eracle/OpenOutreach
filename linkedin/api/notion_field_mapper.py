# linkedin/api/notion_field_mapper.py
"""
Maps LinkedIn profile data to Notion database properties.

Handles profile info, state tracking, timestamps, and message content.
"""
from typing import Dict, Any, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


def _truncate(text: Optional[str], max_len: int = 2000) -> str:
    """Truncate text to Notion's character limit."""
    if not text:
        return ""
    return text[:max_len]


def _get_current_position(profile: Dict[str, Any]) -> Dict[str, str]:
    """Extract current position info (most recent position)."""
    positions = profile.get("positions", [])
    if not positions:
        return {"title": "", "company": ""}

    current = positions[0]  # Positions are ordered, first is current
    return {
        "title": current.get("title", ""),
        "company": current.get("company_name", ""),
    }


def _get_latest_education(profile: Dict[str, Any]) -> str:
    """Extract latest school name."""
    educations = profile.get("educations", [])
    if not educations:
        return ""
    return educations[0].get("school_name", "")


def _get_location(profile: Dict[str, Any]) -> str:
    """Extract location string."""
    location = profile.get("location_name") or ""
    if not location and profile.get("geo"):
        location = profile["geo"].get("defaultLocalizedName", "")
    return location


def _get_industry(profile: Dict[str, Any]) -> str:
    """Extract industry string."""
    if profile.get("industry"):
        return profile["industry"].get("name", "")
    return ""


def _datetime_to_notion(dt: Optional[datetime]) -> Optional[Dict[str, Any]]:
    """Convert datetime to Notion date property format."""
    if not dt:
        return None
    return {"start": dt.isoformat()}


def profile_to_notion_properties(
    profile: Dict[str, Any],
    state: Optional[str] = None,
    created_at: Optional[datetime] = None,
    updated_at: Optional[datetime] = None,
    message_sent: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Convert a LinkedIn profile dict to Notion database properties.

    Args:
        profile: The profile JSON from the Profile model
        state: Current profile state (discovered/enriched/pending/connected/completed/failed)
        created_at: When the profile was first discovered
        updated_at: When the profile was last updated
        message_sent: The follow-up message content if sent

    Returns:
        Dict suitable for Notion API's 'properties' field
    """
    position = _get_current_position(profile)

    properties = {
        # Title property (required for Notion pages)
        "Name": {
            "title": [{"text": {"content": profile.get("full_name", "Unknown")}}]
        },

        # Core profile info
        "LinkedIn URL": {
            "url": profile.get("url")
        },
        "Public ID": {
            "rich_text": [{"text": {"content": profile.get("public_identifier", "")}}]
        },
        "Headline": {
            "rich_text": [{"text": {"content": _truncate(profile.get("headline"))}}]
        },

        # Current position
        "Current Title": {
            "rich_text": [{"text": {"content": _truncate(position["title"])}}]
        },
        "Current Company": {
            "rich_text": [{"text": {"content": _truncate(position["company"])}}]
        },

        # Location & Industry
        "Location": {
            "rich_text": [{"text": {"content": _truncate(_get_location(profile))}}]
        },
        "Industry": {
            "rich_text": [{"text": {"content": _truncate(_get_industry(profile))}}]
        },

        # Education
        "School": {
            "rich_text": [{"text": {"content": _truncate(_get_latest_education(profile))}}]
        },

        # Connection info
        "Connection Degree": {
            "number": profile.get("connection_degree")
        },
    }

    # Add summary if present
    if profile.get("summary"):
        properties["Summary"] = {
            "rich_text": [{"text": {"content": _truncate(profile["summary"])}}]
        }

    # State tracking
    if state:
        properties["State"] = {
            "select": {"name": state}
        }

    # Timestamps
    if created_at:
        properties["Created At"] = {
            "date": _datetime_to_notion(created_at)
        }

    if updated_at:
        properties["Updated At"] = {
            "date": _datetime_to_notion(updated_at)
        }

    # Message sent
    if message_sent:
        properties["Message Sent"] = {
            "rich_text": [{"text": {"content": _truncate(message_sent)}}]
        }

    return properties


def url_to_notion_properties(url: str) -> Dict[str, Any]:
    """
    Create minimal Notion properties for a new URL-only entry.

    Used when adding profiles from Notion input that don't have profile data yet.
    """
    # Extract public_identifier from URL
    public_id = ""
    if "/in/" in url:
        public_id = url.split("/in/")[1].rstrip("/").split("?")[0]

    return {
        "Name": {
            "title": [{"text": {"content": public_id or "New Profile"}}]
        },
        "LinkedIn URL": {
            "url": url
        },
        "Public ID": {
            "rich_text": [{"text": {"content": public_id}}]
        },
        "State": {
            "select": {"name": "discovered"}
        },
    }
