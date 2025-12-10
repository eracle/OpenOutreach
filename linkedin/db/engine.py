# linkedin/db/engine.py
import logging
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session

from linkedin.api.logging import log_profiles
from linkedin.conf import get_account_config
from linkedin.db.models import Base, Profile as DbProfile, CampaignRun, _make_short_run_id

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
            unsynced = db_session.query(DbProfile).filter_by(
                scraped=True,
                cloud_synced=False
            ).all()

            if not unsynced:
                logger.info("All profiles already synced")
                return

            payload = [p.profile for p in unsynced if p.profile]
            if not payload:
                return

            success = log_profiles(payload)

            if success:
                for p in unsynced:
                    p.cloud_synced = True
                db_session.commit()
                logger.info("SUCCESS: Synced %s new profile(s) to cloud", len(payload))
            else:
                logger.error("Cloud sync failed — will retry on next close()")

    @classmethod
    def from_handle(cls, handle: str) -> "Database":
        logger.info("Spinning up DB for @%s", handle)
        config = get_account_config(handle)
        db_path = config["db_path"]
        logger.debug("DB path → %s", db_path)
        return cls(db_path)


# ─────────────────────────────────────────────────────────────────────────────
# CAMPAIGN FUNCTIONS — ONLY AccountSession
# ─────────────────────────────────────────────────────────────────────────────
def has_campaign_run(session: "AccountSession", name: str, input_hash: str) -> bool:
    return session.db_session.query(CampaignRun).filter_by(
        name=name, handle=session.handle, input_hash=input_hash
    ).first() is not None


def mark_campaign_run(
        session: "AccountSession",
        name: str,
        input_hash: str,
        short_id: Optional[str] = None,
) -> str:
    db = session.db_session

    if has_campaign_run(session, name, input_hash):
        existing = db.query(CampaignRun.short_id).filter_by(
            name=name, handle=session.handle, input_hash=input_hash
        ).scalar()
        return existing

    short_id = short_id or _make_short_run_id(name, session.handle, input_hash)
    db.add(CampaignRun(
        name=name,
        handle=session.handle,
        input_hash=input_hash,
        short_id=short_id,
    ))
    db.commit()
    logger.info("Campaign started → %s | @%s | ID: %s", name, session.handle, short_id)
    return short_id


def get_campaign_short_id(session: "AccountSession", name: str, input_hash: str) -> Optional[str]:
    return session.db_session.query(CampaignRun.short_id).filter_by(
        name=name, handle=session.handle, input_hash=input_hash
    ).scalar()


def update_campaign_stats(
        session: "AccountSession",
        name: str,
        input_hash: str,
        *,
        total_profiles: Optional[int] = None,
        increment_enriched: int = 0,
        increment_connect_sent: int = 0,
        increment_accepted: int = 0,
        increment_followup_sent: int = 0,
        increment_completed: int = 0,
):
    db = session.db_session
    run = db.query(CampaignRun).filter_by(
        name=name, handle=session.handle, input_hash=input_hash
    ).first()

    if not run:
        logger.warning("CampaignRun not found → %s | @%s", name, session.handle)
        return

    if total_profiles is not None:
        run.total_profiles = total_profiles
    run.enriched += increment_enriched
    run.connect_sent += increment_connect_sent
    run.accepted += increment_accepted
    run.followup_sent += increment_followup_sent
    run.completed += increment_completed
    db.commit()

    logger.debug("Stats → %s | "
                 "Enriched:%s Connect:%s "
                 "Accepted:%s Followup:%s Done:%s",
                 run.short_id,
                 run.enriched, run.connect_sent,
                 run.accepted, run.followup_sent, run.completed)


def get_campaign_stats(session: "AccountSession", name: str, input_hash: str) -> Optional[dict]:
    run = session.db_session.query(CampaignRun).filter_by(
        name=name, handle=session.handle, input_hash=input_hash
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
