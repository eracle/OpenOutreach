# linkedin/database.py
import logging
from typing import Optional, Dict, Any

from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker, scoped_session

from linkedin.conf import get_account_config
from linkedin.db_models import Base, Profile as DbProfile

logger = logging.getLogger(__name__)


class Database:
    """
    One instance = one account's database.
    Fully isolated. No global state. Thread-safe.
    """

    def __init__(self, db_path: str):
        db_url = f"sqlite:///{db_path}"
        logger.info(f"Initializing database at {db_path}")
        self.engine = create_engine(db_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=self.engine)  # create tables if missing
        logger.debug("Database tables ensured (create_all ran)")

        session_factory = sessionmaker(bind=self.engine)
        self.Session = scoped_session(session_factory)

    def get_session(self):
        """Returns a thread-local session (safe for async/multiprocessing too)"""
        return self.Session()

    def close(self):
        """Optional: clean up"""
        logger.debug("Closing databaseEngine session (scoped_session.remove())")
        self.Session.remove()

    @classmethod
    def from_handle(cls, handle: str) -> "Database":
        """
        Convenience factory: creates a fully configured Database instance for a given account handle.
        """
        logger.info(f"Creating Database instance for account handle: {handle}")
        config = get_account_config(handle)
        db_path = config["db_path"]
        logger.debug(f"Resolved db_path for {handle}: {db_path}")
        return cls(db_path)


def save_profile(session, profile_data: Dict[str, Any], raw_json: Dict[str, Any], linkedin_url: str):
    """
    Saves or updates a profile (both parsed data and raw JSON).
    Sets cloud_synced=False on insert.
    """
    existing = session.query(DbProfile).filter_by(linkedin_url=linkedin_url).first()

    if existing:
        logger.debug(f"Updating existing profile: {linkedin_url}")
        existing.data = profile_data
        existing.raw_json = raw_json
        existing.updated_at = func.now()
    else:
        logger.info(f"Saving new profile: {linkedin_url}")
        db_profile = DbProfile(
            linkedin_url=linkedin_url,
            data=profile_data,
            raw_json=raw_json,
            cloud_synced=False,
        )
        session.add(db_profile)

    try:
        session.commit()
        logger.debug(f"Successfully committed profile: {linkedin_url}")
    except Exception as e:
        session.rollback()
        logger.error(f"Failed to save profile {linkedin_url}: {e}")
        raise


def get_profile(session, linkedin_url: str) -> Optional[Dict[str, Any]]:
    """
    Returns parsed profile data if exists in DB, else None.
    """
    result = session.query(DbProfile).filter_by(linkedin_url=linkedin_url).first()
    if result:
        logger.debug(f"Cache hit for profile: {linkedin_url}")
        return result.data
    else:
        logger.debug(f"Cache miss for profile: {linkedin_url}")
        return None
