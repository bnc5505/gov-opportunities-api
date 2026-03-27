"""
Shared fixtures for all tests.
Uses an in-memory SQLite database so tests never touch the real DB.
"""

import sys
import os
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# point imports at the app folder
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from models import Base, State, Source, Opportunity, OpportunityStatus, OpportunityType
from database import get_db
from main import app

# StaticPool keeps a single connection so all sessions share the same in-memory DB.
# Without it each new connection gets an empty database and sees no tables.
engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def reset_db():
    # fresh tables for every test
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(reset_db):
    # reset_db listed explicitly so tables exist before any request is made
    return TestClient(app)


@pytest.fixture
def db():
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def seed_state(db):
    state = State(code="PA", name="Pennsylvania", is_active=True)
    db.add(state)
    db.commit()
    db.refresh(state)
    return state


@pytest.fixture
def seed_source(db, seed_state):
    source = Source(
        name="PA DCED",
        url="https://dced.pa.gov/programs/",
        state_id=seed_state.id,
        scraper_type="scraper",
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


@pytest.fixture
def seed_opportunity(db, seed_state, seed_source):
    opp = Opportunity(
        title="Small Business Grant Program",
        description="Funding for small businesses in Pennsylvania.",
        summary="Up to $50,000 for eligible PA small businesses.",
        opportunity_type=OpportunityType.GRANT,
        status=OpportunityStatus.ACTIVE,
        state_id=seed_state.id,
        source_id=seed_source.id,
        application_url="https://dced.pa.gov/apply",
        opportunity_url="https://dced.pa.gov/programs/sbgp",
        award_min=5000.0,
        award_max=50000.0,
        rolling=False,
        eligibility_organization=True,
        eligibility_individual=False,
        data_quality_score=0.85,
        needs_review=False,
        industry="Small Business",
    )
    db.add(opp)
    db.commit()
    db.refresh(opp)
    return opp
