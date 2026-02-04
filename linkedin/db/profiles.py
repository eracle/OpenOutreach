# linkedin/db/profiles.py
import json
import logging
from typing import Dict, Any, Optional
from typing import List
from urllib.parse import urlparse, unquote

import pandas as pd
from sqlalchemy import func
from termcolor import colored

from linkedin.db.models import Profile
from linkedin.navigation.enums import ProfileState

logger = logging.getLogger(__name__)


def add_profile_urls(session: "AccountSession", urls: List[str]):
    if not urls:
        return

    public_ids = {pid for url in urls if (pid := url_to_public_id(url))}
    if not public_ids:
        return

    db = session.db_session
    to_insert = [{"public_identifier": pid} for pid in public_ids]
    db.execute(
        Profile.__table__.insert()
        .prefix_with("OR IGNORE")
        .values(to_insert)
    )
    db.commit()

    logger.debug(f"Discovered {len(public_ids)} unique LinkedIn profiles")


def save_scraped_profile(
        session: "AccountSession",
        url: str,
        profile: Dict[str, Any],
        data: Optional[Dict[str, Any]] = None,
        notion_page_id: Optional[str] = None,
):
    public_id = url_to_public_id(url)
    if not public_id:
        logger.warning(f"Invalid LinkedIn URL, cannot save profile: {url}")
        return

    db = session.db_session

    # Get existing or create new instance
    profile_db = db.get(Profile, public_id)
    if profile_db is None:
        profile_db = Profile(public_identifier=public_id)
        db.add(profile_db)
        logger.debug(f"New profile created in DB: {public_id}")
    else:
        logger.debug(f"Updating existing profile: {public_id}")

    # Now safely update fields
    profile_db.profile = profile
    profile_db.data = data
    profile_db.cloud_synced = False
    # Force re-sync on next close()
    profile_db.updated_at = func.now()
    profile_db.state = ProfileState.ENRICHED.value

    # Store Notion page ID if provided (for direct updates later)
    if notion_page_id:
        profile_db.notion_page_id = notion_page_id

    db.commit()

    debug_profile_preview(profile) if logger.isEnabledFor(logging.DEBUG) else None

    logger.debug(f"SUCCESS: Saved enriched profile → {public_id}")


def get_next_url_to_scrape(session: "AccountSession", limit: int = 1) -> List[str]:
    rows = (session.db_session
            .query(Profile.public_identifier)
            .filter(Profile.state == ProfileState.DISCOVERED.value)
            .limit(limit)
            .all())
    return [public_id_to_url(row.public_identifier) for row in rows]


def count_pending_scrape(session: "AccountSession") -> int:
    return (session.db_session
            .query(Profile)
            .filter(Profile.state == ProfileState.DISCOVERED.value)
            .count())


def url_to_public_id(url: str) -> str:
    """
    Strict LinkedIn public ID extractor:
    - Path MUST start with /in/
    - Returns the second segment, percent-decoded
    - Anything else → raises ValueError
    """
    if not url:
        raise ValueError("Empty URL")

    path = urlparse(url.strip()).path
    parts = path.strip("/").split("/")

    if len(parts) < 2 or parts[0] != "in":
        raise ValueError(f"Not a valid /in/ profile URL: {url!r}")

    public_id = parts[1]
    return unquote(public_id)


def public_id_to_url(public_id: str) -> str:
    """
    Convert public_identifier back to a clean LinkedIn profile URL.

    You can choose www or not — both work, www is slightly more common.
    """
    if not public_id:
        return ""
    public_id = public_id.strip("/")
    return f"https://www.linkedin.com/in/{public_id}/"


def get_profile_from_url(session: "AccountSession", url: str):
    public_identifier = url_to_public_id(url)
    if not public_identifier:
        return None

    return get_profile(session, public_identifier)


def get_profile(session: "AccountSession", public_identifier: str) -> Any:
    return session.db_session \
        .query(Profile) \
        .filter_by(public_identifier=public_identifier) \
        .first()


def set_profile_state(
    session: "AccountSession",
    public_identifier: str,
    new_state: str,
    message_sent: Optional[str] = None,
):
    db = session.db_session
    row = db.get(Profile, public_identifier)
    if not row:
        row = Profile(public_identifier=public_identifier, state=new_state)
        db.add(row)
    else:
        row.state = new_state

    # Save message content if provided (when state is COMPLETED)
    if message_sent is not None:
        row.message_sent = message_sent

    # Mark for re-sync to Notion
    row.cloud_synced = False

    db.commit()

    log_msg = None
    match new_state:
        case ProfileState.DISCOVERED:
            log_msg = colored("DISCOVERED", "green")
        case ProfileState.ENRICHED:
            log_msg = colored("ENRICHED", "yellow", attrs=["bold"])
        case ProfileState.PENDING:
            log_msg = colored("PENDING", "yellow", attrs=["bold"])
        case ProfileState.CONNECTED:
            log_msg = colored("CONNECTED", "green")
        case ProfileState.COMPLETED:
            log_msg = colored("COMPLETED", "green", attrs=["bold"])
        case _:
            log_msg = colored("ERROR", "red", attrs=["bold"])

    logger.info(f"{public_identifier} {log_msg}")


def debug_profile_preview(enriched):
    pretty = json.dumps(enriched, indent=2, ensure_ascii=False, default=str)
    preview_lines = pretty.splitlines()[:3]
    logger.debug("=== ENRICHED PROFILE PREVIEW ===\n%s\n...", '\n'.join(preview_lines))


def get_updated_at_df(session: "AccountSession", public_identifiers: List[str]) -> pd.DataFrame:
    """
    Return a DataFrame with public_identifier and updated_at for existing profiles.
    """
    if not public_identifiers:
        return pd.DataFrame(columns=["public_identifier", "updated_at"])

    db = session.db_session

    results = (
        db.query(Profile.public_identifier, Profile.updated_at)
        .filter(Profile.public_identifier.in_(public_identifiers))
        .all()
    )

    if not results:
        return pd.DataFrame(columns=["public_identifier", "updated_at"])

    df = pd.DataFrame(results, columns=["public_identifier", "updated_at"])

    logger.debug(f"Retrieved updated_at for {len(df)} profiles from DB")

    return df
