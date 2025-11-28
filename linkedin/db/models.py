# linkedin/db_models.py

import hashlib

from sqlalchemy import Column, String, JSON, DateTime, Boolean
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func

Base = declarative_base()


def _make_short_run_id(name: str, handle: str, input_hash: str) -> str:
    """12-char deterministic ID – perfect for logs + Temporal workflow IDs"""
    return hashlib.sha256(f"{name}|{handle}|{input_hash}".encode()).hexdigest()[:12]


class CampaignRun(Base):
    __tablename__ = "campaign_runs"

    # Unique triple that identifies an exact campaign run
    name = Column(String, primary_key=True)  # user-chosen campaign name
    handle = Column(String, primary_key=True)  # LinkedIn account handle
    input_hash = Column(String(64), primary_key=True)  # sha256 hex of filters/query

    # When we queued this campaign
    run_at = Column(DateTime, server_default=func.now(), nullable=False)

    # Human-readable short ID (great for Temporal workflow_id and logs)
    short_id = Column(String(12), nullable=False, unique=True, index=True)

    def __init__(self, name: str, handle: str, input_hash: str, short_id: str | None = None, **kwargs):
        super().__init__(**kwargs)
        self.name = name
        self.handle = handle
        self.input_hash = input_hash
        self.short_id = short_id or _make_short_run_id(name, handle, input_hash)


class Profile(Base):
    __tablename__ = 'profiles'

    linkedin_url = Column(String, primary_key=True)

    # Parsed / cleaned data (what you return from get_profile)
    data = Column(JSON, nullable=True)

    # Full raw JSON from LinkedIn's API (for debugging, re-parsing, etc.)
    raw_json = Column(JSON, nullable=True)

    # Whether this profile has been sent to your backend / cloud / CRM
    cloud_synced = Column(Boolean, default=False, server_default='false', nullable=False)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class Company(Base):
    __tablename__ = 'companies'

    linkedin_url = Column(String, primary_key=True)
    data = Column(JSON)
    raw_json = Column(JSON, nullable=True)
    cloud_synced = Column(Boolean, default=False, server_default='false', nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
