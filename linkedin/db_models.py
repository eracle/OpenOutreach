# linkedin/db_models.py

from sqlalchemy import Column, String, JSON, DateTime, Boolean
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func

Base = declarative_base()


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
