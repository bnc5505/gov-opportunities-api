"""
Pipeline logic tests — covers junk detection, deadline normalization,
deduplication, and the is_live_ready quality gate.
No database or network calls needed here.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pipeline"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from load_scraped_grants import (
    is_junk,
    normalize_deadline,
    dedup_key,
    _sanitize_deadline,
    _title_has_grant_signal,
    cap_award,
)
from sync_opportunities import is_live_ready, HIGH_SCORE, MIN_AWARD


# ---------------------------------------------------------------------------
# Junk detection
# ---------------------------------------------------------------------------

def test_exact_junk_title():
    assert is_junk({"title": "Library"})
    assert is_junk({"title": "About"})
    assert is_junk({"title": "Home"})


def test_junk_title_pattern_governor():
    assert is_junk({"title": "The Governor"})
    assert is_junk({"title": "Lt. Governor of Pennsylvania"})


def test_junk_url_nav_segment():
    # nav URL + no description + no award = junk
    assert is_junk({
        "title": "About the Agency",
        "opportunity_url": "https://pa.gov/about-us",
        "description": None,
    })


def test_not_junk_with_grant_signal():
    assert not is_junk({
        "title": "Small Business Grant Program",
        "opportunity_url": "https://pa.gov/grant",
    })


def test_not_junk_when_description_present():
    # a borderline title that is NOT in the exact-match junk list
    # but has no grant signal words — passes because description is present
    assert not is_junk({
        "title": "Conservation and Restoration Initiative",
        "description": "Funding available for environmental restoration projects.",
        "opportunity_url": "https://dcnr.pa.gov/programs",
    })


def test_junk_very_short_title_no_data():
    assert is_junk({"title": "Go", "opportunity_url": None, "description": None})


def test_not_junk_real_grant():
    grant = {
        "title": "Environmental Conservation Fund",
        "description": "Supports land conservation efforts statewide.",
        "opportunity_url": "https://dcnr.pa.gov/ecf",
        "award_max": 50000,
        "data_quality_score": 0.75,
    }
    assert not is_junk(grant)


# ---------------------------------------------------------------------------
# Deadline normalization
# ---------------------------------------------------------------------------

def test_normalize_valid_mmddyyyy():
    assert normalize_deadline("03/15/2026") == "03/15/2026"


def test_normalize_iso_format():
    assert normalize_deadline("2026-06-30") == "06/30/2026"


def test_normalize_long_format():
    assert normalize_deadline("June 30, 2026") == "06/30/2026"


def test_normalize_abbrev_month():
    assert normalize_deadline("Jun 30, 2026") == "06/30/2026"


def test_normalize_none_returns_none():
    assert normalize_deadline(None) is None


def test_normalize_empty_returns_none():
    assert normalize_deadline("") is None


def test_normalize_garbage_returns_none():
    assert normalize_deadline("not a date") is None


def test_normalize_strips_whitespace():
    assert normalize_deadline("  2026-09-01  ") == "09/01/2026"


# ---------------------------------------------------------------------------
# Deduplication key
# ---------------------------------------------------------------------------

def test_dedup_key_uses_opportunity_url():
    g = {"opportunity_url": "https://pa.gov/grant/123", "title": "X", "state": "PA"}
    assert dedup_key(g) == "https://pa.gov/grant/123"


def test_dedup_key_falls_back_to_title_state():
    g = {"opportunity_url": None, "title": "My Grant", "state": "PA"}
    assert dedup_key(g) == "PA::my grant"


def test_dedup_key_lowercases_url():
    g = {"opportunity_url": "https://PA.gov/Grant/ABC", "title": "X", "state": "PA"}
    assert dedup_key(g) == "https://pa.gov/grant/abc"


def test_dedup_key_same_grant_same_key():
    g1 = {"opportunity_url": "https://example.gov/grant", "title": "A", "state": "NY"}
    g2 = {"opportunity_url": "https://example.gov/grant", "title": "B", "state": "MD"}
    assert dedup_key(g1) == dedup_key(g2)


def test_dedup_key_different_grants_different_keys():
    g1 = {"opportunity_url": "https://example.gov/grant1", "title": "A", "state": "PA"}
    g2 = {"opportunity_url": "https://example.gov/grant2", "title": "A", "state": "PA"}
    assert dedup_key(g1) != dedup_key(g2)


# ---------------------------------------------------------------------------
# Award cap
# ---------------------------------------------------------------------------

def test_cap_award_below_limit():
    assert cap_award(50000) == 50000


def test_cap_award_above_limit_returns_none():
    assert cap_award(3_000_000_000) is None


def test_cap_award_none_returns_none():
    assert cap_award(None) is None


def test_cap_award_exactly_at_limit():
    # exactly 2B is within limit
    assert cap_award(2_000_000_000) == 2_000_000_000


# ---------------------------------------------------------------------------
# Grant signal words
# ---------------------------------------------------------------------------

def test_grant_signal_present():
    assert _title_has_grant_signal("Small Business Grant Program")
    assert _title_has_grant_signal("Housing Assistance Fund")
    assert _title_has_grant_signal("Community Infrastructure Improvement Initiative")


def test_grant_signal_absent():
    assert not _title_has_grant_signal("Library Hours and Services")
    assert not _title_has_grant_signal("About the Bureau")


# ---------------------------------------------------------------------------
# is_live_ready — the pipeline quality gate
# ---------------------------------------------------------------------------

def _row(**kwargs):
    # base row that passes all checks; override as needed
    defaults = {
        "title": "Valid Grant",
        "application_url": "https://example.gov/apply",
        "data_quality_score": 0.75,
        "status": "active",
        "deadline": "06/30/2026",
        "rolling": False,
        "award_min": 10000.0,
        "award_max": 50000.0,
        "description": None,
        "summary": None,
    }
    defaults.update(kwargs)
    return defaults


def test_is_live_ready_passes():
    assert is_live_ready(_row(), min_score=0.5)


def test_is_live_ready_no_title():
    assert not is_live_ready(_row(title=""), min_score=0.5)
    assert not is_live_ready(_row(title=None), min_score=0.5)


def test_is_live_ready_no_application_url():
    assert not is_live_ready(_row(application_url=""), min_score=0.5)
    assert not is_live_ready(_row(application_url=None), min_score=0.5)


def test_is_live_ready_low_score_low_award():
    # score below HIGH_SCORE and award_min below MIN_AWARD → blocked
    assert not is_live_ready(_row(
        data_quality_score=0.55,
        award_min=1000.0,
    ), min_score=0.5)


def test_is_live_ready_low_score_high_award():
    # score below HIGH_SCORE but award_min >= MIN_AWARD → passes
    assert is_live_ready(_row(
        data_quality_score=0.55,
        award_min=float(MIN_AWARD),
    ), min_score=0.5)


def test_is_live_ready_high_score_no_award():
    # score above HIGH_SCORE → no award required
    assert is_live_ready(_row(
        data_quality_score=float(HIGH_SCORE) + 0.01,
        award_min=None,
        award_max=None,
    ), min_score=0.5)


def test_is_live_ready_score_below_min():
    assert not is_live_ready(_row(data_quality_score=0.1), min_score=0.5)


def test_is_live_ready_wrong_status():
    assert not is_live_ready(_row(status="recently_closed"), min_score=0.5)
    assert not is_live_ready(_row(status="archived"), min_score=0.5)
    assert not is_live_ready(_row(status="unverified"), min_score=0.5)


def test_is_live_ready_no_deadline_no_rolling():
    assert not is_live_ready(_row(deadline=None, rolling=False), min_score=0.5)
    assert not is_live_ready(_row(deadline=None, rolling=None), min_score=0.5)


def test_is_live_ready_rolling_no_deadline():
    # rolling=True means always accepting, deadline not needed
    assert is_live_ready(_row(deadline=None, rolling=True), min_score=0.5)


def test_is_live_ready_active_statuses():
    for status in ("active", "rolling", "expiring_soon"):
        assert is_live_ready(_row(status=status), min_score=0.5)
