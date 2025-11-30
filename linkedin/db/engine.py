# linkedin/database.py
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


def get_campaign_short_id(session, name: str, handle: str, input_hash: str) -> str | None:
    """One-liner you asked for – safe, returns None if not found"""
    return session.query(CampaignRun.short_id).filter_by(
        name=name, handle=handle, input_hash=input_hash
    ).scalar()


def update_campaign_stats(
        session,
        name: str,
        handle: str,
        input_hash: str,
        *,
        total_profiles: int | None = None,
        increment_enriched: int = 0,
        increment_connect_sent: int = 0,
        increment_accepted: int = 0,
        increment_followup_sent: int = 0,
        increment_completed: int = 0,
):
    """
    Call this from your activities – super fast, one query only.
    Example: update_campaign_stats(s, NAME, HANDLE, hash, increment_connect_sent=1)
    """
    run = session.query(CampaignRun).filter_by(
        name=name, handle=handle, input_hash=input_hash
    ).first()

    if not run:
        logger.warning(f"CampaignRun not found for stats update: {name}|{handle}")
        return

    if total_profiles is not None:
        run.total_profiles = total_profiles

    run.enriched += increment_enriched
    run.connect_sent += increment_connect_sent
    run.accepted += increment_accepted
    run.followup_sent += increment_followup_sent
    run.completed += increment_completed

    session.commit()
    logger.debug(f"Campaign stats updated → {run.short_id} | "
                 f"Enriched:{run.enriched} Connect:{run.connect_sent} "
                 f"Accepted:{run.accepted} Followup:{run.followup_sent} Done:{run.completed}")


def get_campaign_stats(session, name: str, handle: str, input_hash: str) -> dict | None:
    """Returns pretty dict for printing or API"""
    run = session.query(CampaignRun).filter_by(
        name=name, handle=handle, input_hash=input_hash
    ).first()
    if not run:
        return None
    return {
        "campaign_id": run.short_id,
        "total_profiles": run.total_profiles,
        "enriched": run.enriched,
        "connect_sent": run.connect_sent,
        "accepted": run.accepted,
        "followup_sent": run.followup_sent,
        "completed": run.completed,
        "last_updated": run.last_updated.isoformat(),
    }
