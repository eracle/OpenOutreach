# linkedin/database.py
from typing import Optional, Dict, Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session

# ← ADD THIS IMPORT ONLY
from linkedin.conf import get_account_config
from linkedin.db_models import Base, Profile as DbProfile


class Database:
    """
    One instance = one account's database.
    Fully isolated. No global state. Thread-safe.
    """

    def __init__(self, db_path: str):
        db_url = f"sqlite:///{db_path}"
        self.engine = create_engine(db_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=self.engine)  # create tables if missing
        session_factory = sessionmaker(bind=self.engine)
        self.Session = scoped_session(session_factory)

    def get_session(self):
        """Returns a thread-local session (safe for async/multiprocessing too)"""
        return self.Session()

    def close(self):
        """Optional: clean up"""
        self.Session.remove()

    # ← NEW: Class method — this is what you wanted!
    @classmethod
    def from_handle(cls, handle: str) -> "Database":
        """
        Convenience factory: creates a fully configured Database instance for a given account handle.
        Uses the per-account db_path from conf.get_account_config().
        """
        config = get_account_config(handle)
        return cls(config["db_path"])


# — your existing helpers (unchanged)
def save_profile(session, profile_data: Dict[str, Any], linkedin_url: str):
    db_profile = session.query(DbProfile).filter_by(linkedin_url=linkedin_url).first()
    if db_profile:
        db_profile.data = profile_data
    else:
        db_profile = DbProfile(linkedin_url=linkedin_url, data=profile_data)
        session.add(db_profile)
    session.commit()


def get_profile(session, linkedin_url: str) -> Optional[Dict[str, Any]]:
    result = session.query(DbProfile).filter_by(linkedin_url=linkedin_url).first()
    return result.data if result else None

