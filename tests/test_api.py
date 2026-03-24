"""
API endpoint tests — covers health check, opportunities CRUD,
filtering, pagination, states, users, and 404 handling.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from models import Opportunity, OpportunityStatus, OpportunityType, State, Source
from datetime import datetime, timedelta


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def test_root(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Government Grants API" in r.json()["message"]


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Opportunities — basic CRUD
# ---------------------------------------------------------------------------

def test_list_opportunities_empty(client):
    r = client.get("/opportunities")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["data"] == []


def test_create_opportunity(client, seed_state):
    payload = {
        "title": "Community Development Grant",
        "description": "Funding for community projects.",
        "opportunity_type": "grant",
        "state_id": seed_state.id,
        "application_url": "https://example.gov/apply",
        "award_min": 1000,
        "award_max": 25000,
        "eligibility_organization": True,
    }
    r = client.post("/opportunities", json=payload)
    assert r.status_code == 201
    body = r.json()
    assert body["title"] == "Community Development Grant"
    assert body["award_max"] == 25000
    assert body["id"] is not None


def test_get_opportunity_by_id(client, seed_opportunity):
    r = client.get(f"/opportunities/{seed_opportunity.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == seed_opportunity.id
    assert body["title"] == seed_opportunity.title


def test_get_opportunity_not_found(client):
    r = client.get("/opportunities/99999")
    assert r.status_code == 404


def test_update_opportunity(client, seed_opportunity):
    r = client.put(f"/opportunities/{seed_opportunity.id}", json={
        "title": "Updated Grant Title",
        "award_max": 75000,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "Updated Grant Title"
    assert body["award_max"] == 75000


def test_update_opportunity_not_found(client):
    r = client.put("/opportunities/99999", json={"title": "Ghost"})
    assert r.status_code == 404


def test_delete_opportunity(client, seed_opportunity):
    r = client.delete(f"/opportunities/{seed_opportunity.id}")
    assert r.status_code == 204
    # confirm it's gone
    r2 = client.get(f"/opportunities/{seed_opportunity.id}")
    assert r2.status_code == 404


def test_delete_opportunity_not_found(client):
    r = client.delete("/opportunities/99999")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Opportunities — list after seeding
# ---------------------------------------------------------------------------

def test_list_opportunities_returns_seeded(client, seed_opportunity):
    r = client.get("/opportunities")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["data"][0]["title"] == seed_opportunity.title


# ---------------------------------------------------------------------------
# Opportunities — filtering
# ---------------------------------------------------------------------------

def test_filter_by_state(client, db, seed_state, seed_source, seed_opportunity):
    # add a NY opportunity
    ny = State(code="NY", name="New York")
    db.add(ny)
    db.commit()
    ny_opp = Opportunity(
        title="NY Arts Grant",
        opportunity_type=OpportunityType.GRANT,
        status=OpportunityStatus.ACTIVE,
        state_id=ny.id,
        application_url="https://arts.ny.gov/apply",
        rolling=True,
        data_quality_score=0.80,
    )
    db.add(ny_opp)
    db.commit()

    r = client.get("/opportunities?state_code=PA")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["data"][0]["title"] == "Small Business Grant Program"

    r2 = client.get("/opportunities?state_code=NY")
    assert r2.json()["total"] == 1
    assert r2.json()["data"][0]["title"] == "NY Arts Grant"


def test_filter_by_rolling(client, db, seed_state, seed_source, seed_opportunity):
    rolling_opp = Opportunity(
        title="Rolling Workforce Grant",
        opportunity_type=OpportunityType.GRANT,
        status=OpportunityStatus.ACTIVE,
        state_id=seed_state.id,
        application_url="https://dli.pa.gov/apply",
        rolling=True,
        data_quality_score=0.70,
    )
    db.add(rolling_opp)
    db.commit()

    r = client.get("/opportunities?rolling=true")
    assert r.status_code == 200
    titles = [item["title"] for item in r.json()["data"]]
    assert "Rolling Workforce Grant" in titles
    assert "Small Business Grant Program" not in titles


def test_filter_by_award_min(client, seed_opportunity):
    # seed_opportunity has award_max=50000
    r = client.get("/opportunities?award_min=10000")
    assert r.json()["total"] == 1

    r2 = client.get("/opportunities?award_min=100000")
    assert r2.json()["total"] == 0


def test_filter_by_award_max(client, seed_opportunity):
    # seed_opportunity has award_min=5000
    r = client.get("/opportunities?award_max=10000")
    assert r.json()["total"] == 1

    r2 = client.get("/opportunities?award_max=1000")
    assert r2.json()["total"] == 0


def test_search_by_title(client, seed_opportunity):
    r = client.get("/opportunities?q=Small Business")
    assert r.json()["total"] == 1

    r2 = client.get("/opportunities?q=nonexistent_xyz_123")
    assert r2.json()["total"] == 0


def test_filter_by_status(client, db, seed_state, seed_opportunity):
    expired = Opportunity(
        title="Expired Grant",
        opportunity_type=OpportunityType.GRANT,
        status=OpportunityStatus.EXPIRED,
        state_id=seed_state.id,
        application_url="https://example.gov/old",
        data_quality_score=0.60,
    )
    db.add(expired)
    db.commit()

    r = client.get("/opportunities?status=active")
    titles = [d["title"] for d in r.json()["data"]]
    assert "Small Business Grant Program" in titles
    assert "Expired Grant" not in titles

    r2 = client.get("/opportunities?status=expired")
    assert r2.json()["data"][0]["title"] == "Expired Grant"


def test_filter_by_industry(client, seed_opportunity):
    r = client.get("/opportunities?industry=Small Business")
    assert r.json()["total"] == 1

    r2 = client.get("/opportunities?industry=Agriculture")
    assert r2.json()["total"] == 0


def test_filter_by_eligibility_organization(client, seed_opportunity):
    r = client.get("/opportunities?eligibility_organization=true")
    assert r.json()["total"] == 1

    r2 = client.get("/opportunities?eligibility_individual=true")
    assert r2.json()["total"] == 0


# ---------------------------------------------------------------------------
# Opportunities — pagination
# ---------------------------------------------------------------------------

def test_pagination(client, db, seed_state):
    for i in range(5):
        db.add(Opportunity(
            title=f"Grant {i}",
            opportunity_type=OpportunityType.GRANT,
            status=OpportunityStatus.ACTIVE,
            state_id=seed_state.id,
            application_url=f"https://example.gov/grant{i}",
            data_quality_score=0.70,
        ))
    db.commit()

    r = client.get("/opportunities?page=1&per_page=2")
    body = r.json()
    assert body["total"] == 5
    assert len(body["data"]) == 2
    assert body["total_pages"] == 3
    assert body["page"] == 1

    r2 = client.get("/opportunities?page=3&per_page=2")
    assert len(r2.json()["data"]) == 1


def test_per_page_max(client):
    # per_page > 100 should be rejected
    r = client.get("/opportunities?per_page=200")
    assert r.status_code == 422


def test_page_must_be_positive(client):
    r = client.get("/opportunities?page=0")
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Opportunities — sorting
# ---------------------------------------------------------------------------

def test_sort_by_deadline(client, db, seed_state):
    now = datetime.utcnow()
    for i, days in enumerate([30, 10, 60]):
        db.add(Opportunity(
            title=f"Grant {i}",
            opportunity_type=OpportunityType.GRANT,
            status=OpportunityStatus.ACTIVE,
            state_id=seed_state.id,
            application_url=f"https://example.gov/{i}",
            deadline=now + timedelta(days=days),
            data_quality_score=0.70,
        ))
    db.commit()

    r = client.get("/opportunities?sort_by=deadline&sort_order=asc")
    titles = [d["title"] for d in r.json()["data"]]
    deadlines = [d["deadline"] for d in r.json()["data"]]
    assert deadlines == sorted(deadlines)


def test_sort_order_invalid(client):
    r = client.get("/opportunities?sort_order=sideways")
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------

def test_list_states_empty(client):
    r = client.get("/states")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_create_and_list_state(client):
    r = client.post("/states", json={"code": "MD", "name": "Maryland"})
    assert r.status_code == 201
    assert r.json()["code"] == "MD"

    r2 = client.get("/states")
    codes = [s["code"] for s in r2.json()]
    assert "MD" in codes


def test_get_state_by_id(client):
    created = client.post("/states", json={"code": "DC", "name": "Washington DC"}).json()
    r = client.get(f"/states/{created['id']}")
    assert r.status_code == 200
    assert r.json()["code"] == "DC"


def test_get_state_not_found(client):
    r = client.get("/states/99999")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def test_create_user(client):
    r = client.post("/users", json={
        "email": "test@example.com",
        "password": "securepassword123",
        "full_name": "Test User",
        "user_type": "individual",
    })
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "test@example.com"
    assert "password" not in body  # password must never be returned
    assert "hashed_password" not in body


def test_duplicate_email_rejected(client):
    payload = {
        "email": "dupe@example.com",
        "password": "securepassword123",
        "user_type": "individual",
    }
    client.post("/users", json=payload)
    r2 = client.post("/users", json=payload)
    assert r2.status_code == 400


def test_password_too_short(client):
    r = client.post("/users", json={
        "email": "short@example.com",
        "password": "abc",
        "user_type": "individual",
    })
    assert r.status_code == 422


def test_invalid_email_rejected(client):
    r = client.post("/users", json={
        "email": "not-an-email",
        "password": "securepassword123",
        "user_type": "individual",
    })
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def test_create_and_list_source(client, seed_state):
    r = client.post("/sources", json={
        "name": "PA DCED",
        "url": "https://dced.pa.gov/programs/",
        "state_id": seed_state.id,
        "scraper_type": "scraper",
    })
    assert r.status_code == 201
    assert r.json()["name"] == "PA DCED"

    r2 = client.get("/sources")
    assert any(s["name"] == "PA DCED" for s in r2.json())
