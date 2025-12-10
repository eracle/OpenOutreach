# linkedin/db/profiles.py
import json
import logging
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse, unquote

from sqlalchemy import func

from linkedin.db.models import Profile
from linkedin.navigation.enums import ProfileState

logger = logging.getLogger(__name__)


def add_profiles_to_campaign(session, profiles):
    add_profile_urls(session, [profile['url'] for profile in profiles])
    [set_profile_state(session, profile['public_identifier'], new_state=ProfileState.DISCOVERED) for profile in profiles]


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


# linkedin/db/profiles.py

def save_scraped_profile(
        session: "AccountSession",
        url: str,
        profile: Dict[str, Any],
        data: Optional[Dict[str, Any]] = None,
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

    db.commit()

    if logger.isEnabledFor(logging.DEBUG):
        debug_profile_preview(profile)

    logger.info(f"SUCCESS: Saved enriched profile → {public_id}")


def get_next_url_to_scrape(session: "AccountSession", limit: int = 1) -> List[str]:
    # Terminal profile states
    to_scrape_states = [ProfileState.DISCOVERED]

    rows = session.db_session \
        .query(Profile.public_identifier) \
        .filter_by(Profile.state.in_(to_scrape_states)) \
        .limit(limit).all()
    return [public_id_to_url(row.public_identifier) for row in rows]


def count_pending_scrape(session: "AccountSession") -> int:
    to_scrape_states = [ProfileState.DISCOVERED]
    return (session.db_session
            .query(Profile)
            .filter_by(Profile.state.in_(to_scrape_states)).count())


def url_to_public_id(url: str) -> str:
    """
    Convert any LinkedIn profile URL → public_identifier (e.g. 'john-doe-1a2b3c4d')

    Examples:
      "https://www.linkedin.com/in/john-doe-1a2b3c4d/?originalSubdomain=fr" → "john-doe-1a2b3c4d"
      "https://linkedin.com/in/alice/" → "alice"
      "http://linkedin.com/in/bob-123/" → "bob-123"
    """
    if not url:
        return ""

    parsed = urlparse(url.strip().lower())
    if not parsed.path:
        return ""

    # Remove leading '/in/' and trailing slash
    path = parsed.path.strip("/")
    if not path.startswith("in/"):
        return ""

    public_id = path[3:]  # strip the "in/"
    if public_id.endswith("/"):
        public_id = public_id[:-1]

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


def set_profile_state(session: "AccountSession", public_identifier, new_state: str):
    db = session.db_session
    row = db.get(Profile, public_identifier)
    if not row:
        row = Profile(public_identifier=public_identifier, state=new_state)
        db.add(row)
    else:
        row.state = new_state
    db.commit()

    log_msg = None
    match new_state:
        case ProfileState.DISCOVERED:
            log_msg = "\033[32mDISCOVERED\033[0m"
        case ProfileState.ENRICHED:
            log_msg = "\033[93mENRICHED\033[0m"
        case ProfileState.CONNECTED:
            log_msg = "\033[32mCONNECTED\033[0m"
        case ProfileState.COMPLETED:
            log_msg = "\033[1;92mCOMPLETED\033[0m"
        case _:
            log_msg = "\033[91mERROR\033[0m"
    logger.info(f"{public_identifier} {log_msg}")


def debug_profile_preview(enriched):
    pretty = json.dumps(enriched, indent=2, ensure_ascii=False, default=str)
    preview_lines = pretty.splitlines()[:12]
    logger.debug("=== ENRICHED PROFILE PREVIEW ===\n%s\n...", '\n'.join(preview_lines))
