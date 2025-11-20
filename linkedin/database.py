from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from linkedin.db_models import Base, Profile as DbProfile, Company as DbCompany
import json
from typing import Optional, Dict, Any


class DatabaseManager:
    def __init__(self):
        self.engine = None
        self.SessionLocal = None
        self._session = None

    def init_db(self, db_url: str):
        """
        Initializes the database engine and session factory.
        """
        self.engine = create_engine(db_url)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        # Reset session if db is re-initialized
        self._session = None

    def create_tables(self):
        """
        Creates all tables in the database.
        """
        if not self.engine:
            raise Exception("Database not initialized. Call init_db() first.")
        Base.metadata.create_all(bind=self.engine)

    def set_session(self, new_session):
        """
        Allows overriding the module-level session, primarily for testing.
        """
        self._session = new_session

    def get_session(self):
        """
        Returns a singleton database session, creating it if it doesn't exist.
        """
        if self._session is None:
            if not self.SessionLocal:
                raise Exception("Database not initialized. Call init_db() first.")
            self._session = self.SessionLocal()
        return self._session


db_manager = DatabaseManager()


def save_profile(session, profile: Dict[str, Any], linkedin_url: str):
    """
    Saves profile JSON data to the database.
    """
    db_profile = session.query(DbProfile).filter_by(linkedin_url=linkedin_url).first()
    if db_profile:
        db_profile.data = profile
    else:
        db_profile = DbProfile(linkedin_url=linkedin_url, data=profile_data)
        session.add(db_profile)
    session.commit()


def get_profile(session, linkedin_url: str) -> Optional[Dict[str, Any]]:
    """
    Retrieves a profile's JSON data from the database by its linkedin_url.
    """
    db_profile = session.query(DbProfile).filter_by(linkedin_url=linkedin_url).first()
    if db_profile:
        return db_profile.data
    return None


def save_company(session, company_data: Dict[str, Any], linkedin_url: str):
    """
    Saves company JSON data to the database.
    """
    db_company = session.query(DbCompany).filter_by(linkedin_url=linkedin_url).first()
    if db_company:
        db_company.data = company_data
    else:
        db_company = DbCompany(linkedin_url=linkedin_url, data=company_data)
        session.add(db_company)
    session.commit()


def get_company(session, linkedin_url: str) -> Optional[Dict[str, Any]]:
    """
    Retrieves a company's JSON data from the database by its linkedin_url.
    """
    db_company = session.query(DbCompany).filter_by(linkedin_url=linkedin_url).first()
    if db_company:
        return db_company.data
    return None
