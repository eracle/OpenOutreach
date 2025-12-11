# linkedin/db/engine.py
import logging
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session

from linkedin.api.cloud_sync import sync_profiles
from linkedin.conf import get_account_config
from linkedin.db.models import Base, Profile
from linkedin.navigation.enums import ProfileState

logger = logging.getLogger(__name__)


class Database:
    """
    One account → one database.
    Profiles are saved instantly using public_identifier as PK.
    Sync to cloud happens ONLY when close() is called.
    """

    def __init__(self, db_path: str):
        db_url = f"sqlite:///{db_path}"
        logger.info("Initializing local DB → %s", Path(db_path).name)
        self.engine = create_engine(db_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=self.engine)
        logger.debug("DB schema ready (tables ensured)")

        session_factory = sessionmaker(bind=self.engine)
        self.Session = scoped_session(session_factory)
        self.db_path = Path(db_path)

    def get_session(self):
        return self.Session()

    def close(self):
        logger.info("DB.close() → syncing all unsynced profiles to cloud...")
        self._sync_all_unsynced_profiles()
        self.Session.remove()
        logger.info("DB closed and fully synced with cloud")

    def _sync_all_unsynced_profiles(self):
        with self.get_session() as db_session:
            # Fixed: was filtering on non-existent `scraped` column
            unsynced = db_session.query(Profile).filter_by(
                cloud_synced=False
            ).filter(Profile.profile.isnot([ProfileState.DISCOVERED.value])).all()

            if not unsynced:
                logger.info("All profiles already synced")
                return

            payload = [p.data for p in unsynced if p.data]
            if not payload:
                return

            success = sync_profiles(payload)

            if success:
                for p in unsynced:
                    p.cloud_synced = True
                db_session.commit()
                logger.info("Synced %s new profile(s) to cloud", len(payload))
            else:
                logger.error("Cloud sync failed — will retry on next close()")

    @classmethod
    def from_handle(cls, handle: str) -> "Database":
        logger.info("Spinning up DB for @%s", handle)
        config = get_account_config(handle)
        db_path = config["db_path"]
        logger.debug("DB path → %s", db_path)
        return cls(db_path)
