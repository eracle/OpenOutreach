# linkedin/db_models.py

from sqlalchemy import Column, String, JSON, DateTime, Boolean, Integer
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func

Base = declarative_base()


class Profile(Base):
    __tablename__ = 'profiles'

    # USING public_identifier as primary key
    public_identifier = Column(String, primary_key=True)

    # Parsed / cleaned data (what you return from get_profile)
    profile = Column(JSON, nullable=True)

    # Full raw JSON from LinkedIn's API (for debugging, re-parsing, etc.)
    data = Column(JSON, nullable=True)

    # Whether this profile has been sent to your backend / cloud / CRM
    cloud_synced = Column(Boolean, default=False, server_default='false', nullable=False)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    state = Column(String, nullable=False, default="discovered")
    message_sent = Column(String, nullable=True)

    # Notion page ID for profiles loaded from Notion (for direct updates)
    notion_page_id = Column(String, nullable=True)