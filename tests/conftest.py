import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from linkedin.db_models import Base

@pytest.fixture
def db_session():
    """A fixture to create an in-memory SQLite database session for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
