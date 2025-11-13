from sqlalchemy import Column, String, JSON, DateTime
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func

Base = declarative_base()

class Profile(Base):
    __tablename__ = 'profiles'

    linkedin_url = Column(String, primary_key=True)
    data = Column(JSON)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class Company(Base):
    __tablename__ = 'companies'

    linkedin_url = Column(String, primary_key=True)
    data = Column(JSON)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
