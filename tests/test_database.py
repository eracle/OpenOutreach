from linkedin import database
from tests.factories import ProfileFactory, CompanyFactory
from sqlalchemy import inspect

def test_create_tables(db_session):
    """
    Tests that the tables are created correctly.
    """
    inspector = inspect(db_session.bind)
    assert "profiles" in inspector.get_table_names()
    assert "companies" in inspector.get_table_names()

def test_save_and_get_profile(db_session):
    """
    Tests that a profile can be saved to and retrieved from the database.
    """
    profile = ProfileFactory()
    
    database.save_profile(db_session, profile)
    retrieved_profile = database.get_profile(db_session, profile.linkedin_url)

    assert retrieved_profile is not None
    assert retrieved_profile.linkedin_url == profile.linkedin_url
    assert retrieved_profile.public_id == profile.public_id
    assert retrieved_profile.first_name == profile.first_name
    assert retrieved_profile.last_name == profile.last_name
    assert retrieved_profile.headline == profile.headline
    assert retrieved_profile.summary == profile.summary
    assert retrieved_profile.country == profile.country
    assert retrieved_profile.city == profile.city
    assert retrieved_profile.experience == profile.experience
    assert retrieved_profile.education == profile.education
    assert retrieved_profile.skills == profile.skills

def test_save_and_get_company(db_session):
    """
    Tests that a company can be saved to and retrieved from the database.
    """
    company = CompanyFactory()
    
    database.save_company(db_session, company)
    retrieved_company = database.get_company(db_session, company.linkedin_url)

    assert retrieved_company is not None
    assert retrieved_company.linkedin_url == company.linkedin_url
    assert retrieved_company.name == company.name
    assert retrieved_company.tagline == company.tagline
    assert retrieved_company.about == company.about
    assert retrieved_company.website == company.website
    assert retrieved_company.industry == company.industry
    assert retrieved_company.company_size == company.company_size
    assert retrieved_company.headquarters == company.headquarters
