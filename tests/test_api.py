"""
API endpoint tests — covers health check, opportunities CRUD,
filtering, pagination, states, users, and 404 handling.
"""

import sys
import os
import pytest
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


# ===========================================================================
# 10-opportunity integration tests
# Fixture seeds 10 varied opportunities: PA×6, NY×4
# ===========================================================================

@pytest.fixture
def ten_opps(db, seed_state, seed_source):
    """
    10 opportunities across 2 states with varied award sizes and statuses.

    PA (6):  Small(10k), Mid(50k), Large(250k), Rolling(75k), Individual(5k), Expired(20k)
    NY (4):  Arts(25k), Tech(500k), Workforce(100k), Housing(1M)

    award_min filter logic:  award_max >= requested_min
    So ?award_min=50000 matches any opp whose award_max >= 50000.
    """
    ny = State(code="NY", name="New York", is_active=True)
    db.add(ny)
    db.commit()
    db.refresh(ny)

    rows = [
        Opportunity(title="PA Small Grant",      state_id=seed_state.id, source_id=seed_source.id,
                    award_min=1_000,    award_max=10_000,      eligibility_organization=True,
                    status=OpportunityStatus.ACTIVE,  opportunity_type=OpportunityType.GRANT,
                    application_url="https://pa.gov/1", data_quality_score=0.80),
        Opportunity(title="PA Mid Grant",        state_id=seed_state.id, source_id=seed_source.id,
                    award_min=10_000,   award_max=50_000,      eligibility_organization=True,
                    status=OpportunityStatus.ACTIVE,  opportunity_type=OpportunityType.GRANT,
                    application_url="https://pa.gov/2", data_quality_score=0.75),
        Opportunity(title="PA Large Grant",      state_id=seed_state.id, source_id=seed_source.id,
                    award_min=50_000,   award_max=250_000,     eligibility_organization=True,
                    status=OpportunityStatus.ACTIVE,  opportunity_type=OpportunityType.GRANT,
                    application_url="https://pa.gov/3", data_quality_score=0.85),
        Opportunity(title="PA Rolling Grant",    state_id=seed_state.id, source_id=seed_source.id,
                    award_min=5_000,    award_max=75_000,      eligibility_organization=True,
                    status=OpportunityStatus.ACTIVE,  opportunity_type=OpportunityType.GRANT,
                    application_url="https://pa.gov/4", rolling=True, data_quality_score=0.70),
        Opportunity(title="PA Individual Grant", state_id=seed_state.id, source_id=seed_source.id,
                    award_min=500,      award_max=5_000,       eligibility_individual=True,
                    status=OpportunityStatus.ACTIVE,  opportunity_type=OpportunityType.GRANT,
                    application_url="https://pa.gov/5", data_quality_score=0.65),
        Opportunity(title="PA Expired Grant",    state_id=seed_state.id, source_id=seed_source.id,
                    award_min=2_000,    award_max=20_000,      eligibility_organization=True,
                    status=OpportunityStatus.EXPIRED, opportunity_type=OpportunityType.GRANT,
                    application_url="https://pa.gov/6", data_quality_score=0.60),
        Opportunity(title="NY Arts Grant",       state_id=ny.id,         source_id=seed_source.id,
                    award_min=5_000,    award_max=25_000,      eligibility_organization=True,
                    status=OpportunityStatus.ACTIVE,  opportunity_type=OpportunityType.GRANT,
                    application_url="https://ny.gov/1", data_quality_score=0.80),
        Opportunity(title="NY Tech Grant",       state_id=ny.id,         source_id=seed_source.id,
                    award_min=50_000,   award_max=500_000,     eligibility_organization=True,
                    status=OpportunityStatus.ACTIVE,  opportunity_type=OpportunityType.GRANT,
                    application_url="https://ny.gov/2", data_quality_score=0.90),
        Opportunity(title="NY Workforce Grant",  state_id=ny.id,         source_id=seed_source.id,
                    award_min=10_000,   award_max=100_000,     eligibility_organization=True,
                    status=OpportunityStatus.ACTIVE,  opportunity_type=OpportunityType.GRANT,
                    application_url="https://ny.gov/3", data_quality_score=0.75),
        Opportunity(title="NY Housing Grant",    state_id=ny.id,         source_id=seed_source.id,
                    award_min=100_000,  award_max=1_000_000,   eligibility_organization=True,
                    status=OpportunityStatus.ACTIVE,  opportunity_type=OpportunityType.GRANT,
                    application_url="https://ny.gov/4", data_quality_score=0.85),
    ]
    for o in rows:
        db.add(o)
    db.commit()
    return rows


# ---------------------------------------------------------------------------
# 1. GET /opportunities  — no filters
# ---------------------------------------------------------------------------

def test_list_no_filters_total(client, ten_opps):
    r = client.get("/opportunities")
    assert r.status_code == 200
    assert r.json()["total"] == 10


def test_list_response_envelope_schema(client, ten_opps):
    body = client.get("/opportunities").json()
    for key in ("total", "page", "per_page", "total_pages", "data"):
        assert key in body, f"Missing envelope key: {key!r}"
    assert body["page"] == 1
    assert body["per_page"] == 20   # default per_page
    assert body["total_pages"] == 1  # 10 items ÷ 20 per page = 1 page


def test_list_item_schema(client, ten_opps):
    items = client.get("/opportunities").json()["data"]
    assert len(items) == 10
    required_fields = {
        "id", "title", "status", "opportunity_type",
        "eligibility_individual", "eligibility_organization",
        "award_min", "award_max", "rolling",
    }
    for item in items:
        missing = required_fields - item.keys()
        assert not missing, f"Item missing fields: {missing}"


# ---------------------------------------------------------------------------
# 2. GET /opportunities?state_code=PA
# ---------------------------------------------------------------------------

def test_filter_state_pa_count(client, ten_opps):
    r = client.get("/opportunities?state_code=PA")
    assert r.status_code == 200
    assert r.json()["total"] == 6


def test_filter_state_pa_all_items_belong_to_pa(client, ten_opps):
    items = client.get("/opportunities?state_code=PA").json()["data"]
    for item in items:
        assert item["state"]["code"] == "PA", (
            f"Expected PA, got {item['state']['code']!r} for {item['title']!r}"
        )


def test_filter_state_ny_count(client, ten_opps):
    assert client.get("/opportunities?state_code=NY").json()["total"] == 4


def test_filter_state_nonexistent_returns_zero(client, ten_opps):
    assert client.get("/opportunities?state_code=ZZ").json()["total"] == 0


# ---------------------------------------------------------------------------
# 3. GET /opportunities?award_min=50000
#    API filter: award_max >= award_min (grants that can pay at least X)
# ---------------------------------------------------------------------------

def test_filter_award_min_50000(client, ten_opps):
    # award_max >= 50000:
    #   PA Mid (50k), PA Large (250k), PA Rolling (75k),
    #   NY Tech (500k), NY Workforce (100k), NY Housing (1M)  → 6
    r = client.get("/opportunities?award_min=50000")
    assert r.status_code == 200
    assert r.json()["total"] == 6


def test_filter_award_min_excludes_small_grants(client, ten_opps):
    titles = [i["title"] for i in client.get("/opportunities?award_min=50000").json()["data"]]
    assert "PA Small Grant" not in titles     # award_max=10k
    assert "PA Individual Grant" not in titles  # award_max=5k
    assert "NY Arts Grant" not in titles      # award_max=25k


def test_filter_award_min_high_ceiling(client, ten_opps):
    # award_max >= 500000 → NY Tech (500k), NY Housing (1M)
    assert client.get("/opportunities?award_min=500000").json()["total"] == 2


# ---------------------------------------------------------------------------
# 4. GET /opportunities?state_code=PA&award_min=50000&per_page=5
# ---------------------------------------------------------------------------

def test_combined_state_and_award_filter(client, ten_opps):
    # PA + award_max >= 50000: PA Mid(50k), PA Large(250k), PA Rolling(75k) → 3
    r = client.get("/opportunities?state_code=PA&award_min=50000")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    for item in body["data"]:
        assert item["state"]["code"] == "PA"
        assert item["award_max"] >= 50_000


def test_combined_filter_with_per_page(client, ten_opps):
    # Same 3 PA+50k results, but per_page=2 → page 1 has 2, total_pages=2
    r = client.get("/opportunities?state_code=PA&award_min=50000&per_page=2")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert len(body["data"]) == 2
    assert body["total_pages"] == 2
    assert body["per_page"] == 2


def test_combined_filter_page_2(client, ten_opps):
    r = client.get("/opportunities?state_code=PA&award_min=50000&per_page=2&page=2")
    body = r.json()
    assert len(body["data"]) == 1  # 3rd item on page 2


# ---------------------------------------------------------------------------
# 5. GET /opportunities/{id}
# ---------------------------------------------------------------------------

def test_get_by_id_200(client, ten_opps):
    first_id = ten_opps[0].id
    r = client.get(f"/opportunities/{first_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == first_id
    assert body["title"] == "PA Small Grant"


def test_get_by_id_detail_schema(client, ten_opps):
    # Detail endpoint returns OpportunityResponse which has more fields than list item
    body = client.get(f"/opportunities/{ten_opps[0].id}").json()
    detail_only_fields = {"created_at", "updated_at", "categories", "eligible_applicants"}
    for field in detail_only_fields:
        assert field in body, f"Detail response missing: {field!r}"


def test_get_by_id_404(client, ten_opps):
    r = client.get("/opportunities/99999")
    assert r.status_code == 404
    assert "detail" in r.json()


def test_get_by_id_invalid_type_422(client):
    r = client.get("/opportunities/not-an-integer")
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# 6. GET /states
# ---------------------------------------------------------------------------

def test_list_states_with_seeded_data(client, ten_opps):
    # ten_opps seeds PA (via seed_state) + NY
    r = client.get("/states")
    assert r.status_code == 200
    codes = {s["code"] for s in r.json()}
    assert "PA" in codes
    assert "NY" in codes


def test_states_response_schema(client, seed_state):
    states = client.get("/states").json()
    assert len(states) >= 1
    for s in states:
        for field in ("id", "code", "name", "is_active", "created_at"):
            assert field in s, f"State missing field: {field!r}"


def test_states_returns_list_not_paginated(client, seed_state):
    # /states returns a raw list, not the paginated envelope
    body = client.get("/states").json()
    assert isinstance(body, list), "Expected list, got paginated envelope"
    assert "total" not in body  # no pagination wrapper


# ---------------------------------------------------------------------------
# 7. GET /health
# ---------------------------------------------------------------------------

def test_health_status_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_health_response_schema(client):
    body = client.get("/health").json()
    assert "status" in body
    assert isinstance(body["status"], str)


# ---------------------------------------------------------------------------
# Pagination edge cases
# ---------------------------------------------------------------------------

def test_pagination_total_pages_calculation(client, ten_opps):
    body = client.get("/opportunities?per_page=3").json()
    assert body["total"] == 10
    assert body["total_pages"] == 4   # ceil(10/3)
    assert len(body["data"]) == 3


def test_pagination_last_page_partial(client, ten_opps):
    body = client.get("/opportunities?per_page=3&page=4").json()
    assert len(body["data"]) == 1    # 10 items, page 4 of 4 = 1 item


def test_pagination_page_beyond_last_returns_empty(client, ten_opps):
    body = client.get("/opportunities?per_page=10&page=2").json()
    assert body["total"] == 10
    assert body["data"] == []        # page 2 with per_page=10 is empty


def test_pagination_response_page_matches_request(client, ten_opps):
    body = client.get("/opportunities?per_page=5&page=2").json()
    assert body["page"] == 2
    assert body["per_page"] == 5


# ---------------------------------------------------------------------------
# Bugs revealed by integration tests
# ---------------------------------------------------------------------------

def test_bug_limit_param_silently_ignored(client, ten_opps):
    """
    BUG: ?limit=5 is not a declared query parameter.
    FastAPI silently ignores it — callers expecting SQL-style LIMIT get all results.
    The correct parameter is ?per_page=5.
    """
    r = client.get("/opportunities?limit=5")
    assert r.status_code == 200          # no 422 — unknown param accepted
    body = r.json()
    assert body["total"] == 10           # limit was ignored; all 10 returned
    assert len(body["data"]) == 10       # not 5


def test_bug_state_param_wrong_name_silently_ignored(client, ten_opps):
    """
    BUG: ?state=PA uses the wrong parameter name (should be ?state_code=PA).
    FastAPI silently ignores the unknown param — all 10 rows returned instead of 6.
    """
    r = client.get("/opportunities?state=PA")
    assert r.status_code == 200
    assert r.json()["total"] == 10       # filter not applied


def test_sort_by_invalid_field_returns_422(client, ten_opps):
    """Bug 3 fixed: Literal type on sort_by now rejects unknown fields with 422."""
    r = client.get("/opportunities?sort_by=nonexistent_column")
    assert r.status_code == 422


def test_sort_by_valid_fields_accepted(client, ten_opps):
    for field in ("deadline", "award_min", "award_max", "created_at", "title"):
        r = client.get(f"/opportunities?sort_by={field}")
        assert r.status_code == 200, f"Expected 200 for sort_by={field!r}"


def test_response_per_page_field_matches_query_param(client, ten_opps):
    """Bug 4 fixed: response field 'per_page' now matches the query param name."""
    body = client.get("/opportunities?per_page=7").json()
    assert "per_page" in body
    assert body["per_page"] == 7
