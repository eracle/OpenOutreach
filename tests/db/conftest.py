# tests/conftest.py
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session

from linkedin.db.models import Base


@pytest.fixture(scope="function")
def db_session():
    """
    Yields a clean, in-memory SQLite session with all tables created.
    Every test gets its own fresh database → no state leaks.
    """
    # In-memory SQLite database (fresh for each test)
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)

    # Create all tables
    Base.metadata.create_all(bind=engine)

    # Scoped session (safe for threads, same pattern you use in production)
    SessionFactory = sessionmaker(bind=engine)
    Session = scoped_session(SessionFactory)

    session = Session()

    yield session  # ← test runs here

    # Cleanup: close session + drop everything
    session.close()
    Base.metadata.drop_all(bind=engine)
    Session.remove()
