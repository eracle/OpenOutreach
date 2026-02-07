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
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    Base.metadata.create_all(bind=engine)

    SessionFactory = sessionmaker(bind=engine)
    Session = scoped_session(SessionFactory)
    session = Session()

    yield session

    session.close()
    Base.metadata.drop_all(bind=engine)
    Session.remove()


class FakeAccountSession:
    """Minimal stand-in for AccountSession — only exposes db_session."""

    def __init__(self, db_session):
        self.db_session = db_session


@pytest.fixture
def fake_session(db_session):
    """An AccountSession-like object backed by the in-memory db_session."""
    return FakeAccountSession(db_session)
