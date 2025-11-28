# linkedin/database.py
import hashlib
import logging
from pathlib import Path
from typing import Optional, Dict, Any

from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker, scoped_session

from linkedin.api.logging import log_profiles
from linkedin.conf import get_account_config
from linkedin.db.models import Base, Profile as DbProfile, CampaignRun, _make_short_run_id

logger = logging.getLogger(__name__)


class Database:
    """
    One account → one database.
    Profiles are saved instantly.
    Sync to cloud happens ONLY when close() is called.
    """

    def __init__(self, db_path: str):
        db_url = f"sqlite:///{db_path}"
        logger.info(f"Initializing database at {db_path}")
        self.engine = create_engine(db_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=self.engine)
        logger.debug("Database tables ensured (create_all ran)")

        session_factory = sessionmaker(bind=self.engine)
        self.Session = scoped_session(session_factory)

        self.db_path = Path(db_path)

    def get_session(self):
        return self.Session()

    def close(self):
        """This is the ONLY place we sync to your backend."""
        logger.info("Database.close() triggered → syncing all unsynced profiles to cloud...")
        self._sync_all_unsynced_profiles()
        self.Session.remove()
        logger.info("Database closed and fully synced.")

    def _sync_all_unsynced_profiles(self):
        with self.get_session() as session:
            unsynced = session.query(DbProfile).filter_by(cloud_synced=False).all()
            if not unsynced:
                logger.info("No unsynced profiles found.")
                return

            payload = [p.data for p in unsynced if p.data]

            success = log_profiles(payload)

            if success:
                for p in unsynced:
                    p.cloud_synced = True
                session.commit()
                logger.info(f"SUCCESS: Synced {len(payload)} profiles to cloud")
            else:
                logger.error("Sync failed — profiles remain unsynced. Will retry next close().")

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

    @staticmethod
    def hash_file(path: Path | str, chunk_size: int = 8192, algorithm: str = "sha256") -> str:
        """
        Compute a stable cryptographic hash of a file's contents.
        Used to detect if the input CSV has changed → new campaign run.

        Returns hex digest (first 16 chars by default for brevity + safety).
        """
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"Cannot hash file: {path} does not exist or is not a file")

        hasher = hashlib.new(algorithm)

        with path.open("rb") as f:
            while chunk := f.read(chunk_size):
                hasher.update(chunk)

        full_hex = hasher.hexdigest()
        short_hex = full_hex[:16]
        logger.debug(f"Hashed file {path.name} → {short_hex} (full: {full_hex})")
        return short_hex


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
        session.add(DbProfile(
            linkedin_url=linkedin_url,
            data=profile_data,
            raw_json=raw_json,
            cloud_synced=False,
        ))

    session.commit()


def get_profile(session, linkedin_url: str) -> Optional[Dict[str, Any]]:
    """
    Returns parsed profile data if exists in DB, else None.
    """
    result = session.query(DbProfile).filter_by(linkedin_url=linkedin_url).first()
    return result.data if result else None


def has_campaign_run(session, name: str, handle: str, input_hash: str) -> bool:
    """Fast check if this exact campaign has already been queued."""
    return session.query(CampaignRun).filter_by(
        name=name,
        handle=handle,
        input_hash=input_hash
    ).first() is not None


def mark_campaign_run(
        session,
        name: str,
        handle: str,
        input_hash: str,
        short_id: str | None = None,
) -> str:
    """
    Record that this campaign was started.
    Idempotent – safe to call multiple times.
    Returns the short_id (useful for Temporal workflow_id).
    """
    if has_campaign_run(session, name, handle, input_hash):
        # Already exists → fetch and return its short_id
        existing = session.query(CampaignRun.short_id).filter_by(
            name=name, handle=handle, input_hash=input_hash
        ).scalar()
        return existing

    if short_id is None:
        short_id = _make_short_run_id(name, handle, input_hash)

    session.add(CampaignRun(
        name=name,
        handle=handle,
        input_hash=input_hash,
        short_id=short_id,
    ))
    session.commit()
    logger.info(f"Campaign run recorded → {name} | {handle} | {short_id}")
    return short_id
