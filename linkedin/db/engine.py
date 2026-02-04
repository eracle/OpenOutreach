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
        self._run_migrations()
        logger.debug("DB schema ready (tables ensured)")

        session_factory = sessionmaker(bind=self.engine)
        self.Session = scoped_session(session_factory)
        self.db_path = Path(db_path)

    def _run_migrations(self):
        """Run schema migrations for existing databases."""
        from sqlalchemy import text, inspect

        inspector = inspect(self.engine)
        columns = [col['name'] for col in inspector.get_columns('profiles')]

        with self.engine.connect() as conn:
            # Migration: Add message_sent column if missing
            if 'message_sent' not in columns:
                logger.info("Migration: Adding 'message_sent' column to profiles table")
                conn.execute(text("ALTER TABLE profiles ADD COLUMN message_sent VARCHAR"))
                conn.commit()

            # Migration: Add notion_page_id column if missing
            if 'notion_page_id' not in columns:
                logger.info("Migration: Adding 'notion_page_id' column to profiles table")
                conn.execute(text("ALTER TABLE profiles ADD COLUMN notion_page_id VARCHAR"))
                conn.commit()

    def get_session(self):
        return self.Session()

    def close(self):
        logger.info("DB.close() → syncing all unsynced profiles to cloud...")
        self._sync_all_unsynced_profiles()
        self.Session.remove()
        logger.info("DB closed and fully synced with cloud")

    def _sync_all_unsynced_profiles(self):
        with self.get_session() as db_session:
            # Sync all unsynced profiles that have profile data (not just discovered)
            unsynced = db_session.query(Profile).filter_by(
                cloud_synced=False
            ).filter(Profile.state != ProfileState.DISCOVERED.value).all()

            if not unsynced:
                logger.info("All profiles already synced")
                return

            # Debug: log profile data availability
            for p in unsynced:
                has_data = bool(p.profile and p.profile.get("full_name"))
                logger.debug("Profile %s: state=%s, has_enriched_data=%s",
                            p.public_identifier, p.state, has_data)

            # Pass full Profile rows to sync (not just data) for state/timestamps
            profiles_to_sync = [p for p in unsynced if p.profile]
            if not profiles_to_sync:
                logger.warning("No profiles with data to sync (all had empty profile JSON)")
                return

            success = sync_profiles(profiles_to_sync)

            if success:
                for p in unsynced:
                    p.cloud_synced = True
                db_session.commit()
                logger.info("Synced %s new profile(s) to cloud", len(profiles_to_sync))
            else:
                logger.error("Cloud sync failed — will retry on next close()")

    @classmethod
    def from_handle(cls, handle: str) -> "Database":
        logger.info("Spinning up DB for @%s", handle)
        config = get_account_config(handle)
        db_path = config["db_path"]
        logger.debug("DB path → %s", db_path)
        return cls(db_path)
