from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from linkedin.db_models import Base, Profile as DbProfile, Company as DbCompany
from linkedin.models import Profile, Company
import json

engine = create_engine('sqlite:///linkedin.db')
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def create_tables():
    """
    Creates all tables in the database.
    """
    Base.metadata.create_all(bind=engine)

def get_session():
    """
    Returns a new database session.
    """
    return SessionLocal()

def save_profile(session, profile: Profile):
    """
    Saves a Profile object to the database.
    """
    db_profile = session.query(DbProfile).filter_by(linkedin_url=profile.linkedin_url).first()
    if db_profile:
        db_profile.data = profile.model_dump()
    else:
        db_profile = DbProfile(linkedin_url=profile.linkedin_url, data=profile.model_dump())
        session.add(db_profile)
    session.commit()

def get_profile(session, linkedin_url: str) -> Profile | None:
    """
    Retrieves a Profile object from the database by its linkedin_url.
    """
    db_profile = session.query(DbProfile).filter_by(linkedin_url=linkedin_url).first()
    if db_profile:
        return Profile(**db_profile.data)
    return None

def save_company(session, company: Company):
    """
    Saves a Company object to the database.
    """
    db_company = session.query(DbCompany).filter_by(linkedin_url=company.linkedin_url).first()
    if db_company:
        db_company.data = company.model_dump()
    else:
        db_company = DbCompany(linkedin_url=company.linkedin_url, data=company.model_dump())
        session.add(db_company)
    session.commit()

def get_company(session, linkedin_url: str) -> Company | None:
    """
    Retrieves a Company object from the database by its linkedin_url.
    """
    db_company = session.query(DbCompany).filter_by(linkedin_url=linkedin_url).first()
    if db_company:
        return Company(**db_company.data)
    return None
