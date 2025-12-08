from typing import List, Dict, Any, Optional
from urllib.parse import urlparse, unquote

from sqlalchemy import func

from linkedin.db.engine import logger
from linkedin.db.models import Profile as DbProfile
from linkedin.sessions.account import AccountSession


def add_profile_urls(session: AccountSession, urls: List[str]):
    if not urls:
        return

    public_ids = {pid for url in urls if (pid := url_to_public_id(url))}
    if not public_ids:
        return

    db = session.db.get_session()
    to_insert = [{"public_identifier": pid} for pid in public_ids]
    db.execute(
        DbProfile.__table__.insert()
        .prefix_with("OR IGNORE")
        .values(to_insert)
    )
    db.commit()

    logger.debug(f"Discovered {len(public_ids)} unique LinkedIn profiles")
    for pid in public_ids:
        logger.debug("Profile discovered → %s", public_id_to_url(pid))


def save_scraped_profile(
        session: AccountSession,
        url: str,
        profile_data: Dict[str, Any],
        raw_json: Optional[Dict[str, Any]] = None,
):
    public_id = url_to_public_id(url)
    db = session.db.get_session()

    profile = db.get(DbProfile, public_id) or DbProfile(public_identifier=public_id)
    profile.data = profile_data
    profile.raw_json = raw_json
    profile.scraped = True
    profile.cloud_synced = False
    profile.updated_at = func.now()

    db.merge(profile)
    db.commit()
    logger.debug(f"Scraped profile saved → {public_id_to_url(public_id)}")


def get_next_url_to_scrape(session: AccountSession, limit: int = 1) -> List[str]:
    rows = session.db.get_session() \
        .query(DbProfile.public_identifier) \
        .filter_by(scraped=False) \
        .limit(limit).all()
    return [public_id_to_url(row.public_identifier) for row in rows]


def count_pending_scrape(session: AccountSession) -> int:
    return session.db.get_session().query(DbProfile).filter_by(scraped=False).count()


def get_profile_by_public_id(session: AccountSession, public_id: str) -> Optional[Dict[str, Any]]:
    profile = session.db.get_session().get(DbProfile, public_id)
    return profile.data if profile and profile.scraped else None


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
    scheme = "https"
    domain = "linkedin.com"
    return f"{scheme}://{domain}/in/{public_id}/"


def is_profile_already_scraped(session: AccountSession, url: str) -> bool:
    public_id = url_to_public_id(url)
    if not public_id:
        return False

    exists = session.db.get_session() \
        .query(DbProfile.public_identifier) \
        .filter_by(public_identifier=public_id, scraped=True) \
        .first() is not None

    return exists