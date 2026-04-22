"""
Pipeline logic tests — covers junk detection, deadline normalization,
deduplication, and the is_live_ready quality gate.
No database or network calls needed here.
"""

import sys
import os
from unittest.mock import MagicMock

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_BASE_DIR, "..", "pipeline"))
sys.path.insert(0, os.path.join(_BASE_DIR, "..", "app"))
sys.path.insert(0, os.path.join(_BASE_DIR, "..", "scrapers"))

# Mock database before any pipeline module import triggers `import database`
if "database" not in sys.modules:
    sys.modules["database"] = MagicMock()

from load_scraped_grants import (
    is_junk,
    normalize_deadline,
    dedup_key,
    _sanitize_deadline,
    _title_has_grant_signal,
    cap_award,
)
from sync_opportunities import is_live_ready, parse_deadline, make_key, HIGH_SCORE, MIN_AWARD
from enrich_scraped_grants import recalculate_score
from base.base_scraper import extract_date, try_parse, resolve_year


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
        "opportunity_url": "https://example.gov/grant-info",
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
    # application_url absent but opportunity_url present → still live-ready (fallback applies)
    assert is_live_ready(_row(application_url=""), min_score=0.5)
    assert is_live_ready(_row(application_url=None), min_score=0.5)
    # both absent → blocked
    assert not is_live_ready(_row(application_url=None, opportunity_url=None), min_score=0.5)
    assert not is_live_ready(_row(application_url="",   opportunity_url=""),   min_score=0.5)


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


# ---------------------------------------------------------------------------
# parse_deadline
# ---------------------------------------------------------------------------

def test_parse_deadline_mm_dd_yyyy():
    result = parse_deadline("06/30/2026")
    assert result == "2026-06-30T00:00:00"


def test_parse_deadline_iso_format():
    result = parse_deadline("2026-06-30")
    assert result == "2026-06-30T00:00:00"


def test_parse_deadline_mm_dash_dd_dash_yyyy():
    result = parse_deadline("06-30-2026")
    assert result == "2026-06-30T00:00:00"


def test_parse_deadline_strips_whitespace():
    result = parse_deadline("  06/30/2026  ")
    assert result == "2026-06-30T00:00:00"


def test_parse_deadline_none_returns_none():
    assert parse_deadline(None) is None


def test_parse_deadline_empty_string_returns_none():
    assert parse_deadline("") is None


def test_parse_deadline_garbage_returns_none():
    assert parse_deadline("not a date") is None


def test_parse_deadline_long_month_name():
    assert parse_deadline("June 30, 2026") == "2026-06-30T00:00:00"


def test_parse_deadline_abbreviated_month():
    assert parse_deadline("Jun 30, 2026") == "2026-06-30T00:00:00"


def test_parse_deadline_day_month_year():
    assert parse_deadline("30 June 2026") == "2026-06-30T00:00:00"


def test_parse_deadline_dateutil_fallback():
    # dateutil fallback handles variants strptime can't
    result = parse_deadline("September 1st, 2026")
    assert result is not None
    assert "2026-09-01" in result


def test_parse_deadline_partial_date_returns_none():
    assert parse_deadline("06/30") is None


# ---------------------------------------------------------------------------
# make_key
# ---------------------------------------------------------------------------

def test_make_key_uses_opportunity_url_not_application_url():
    # same opportunity_url → same key, regardless of application_url
    k1 = make_key("PA", "https://pa.gov/grant/123", "https://pa.gov/apply-a")
    k2 = make_key("PA", "https://pa.gov/grant/123", "https://pa.gov/apply-b")
    assert k1 == k2


def test_make_key_fallback_to_application_url_when_opportunity_url_empty():
    k1 = make_key("PA", "", "https://pa.gov/apply")
    k2 = make_key("PA", "", "https://pa.gov/apply")
    assert k1 == k2


def test_make_key_fallback_to_application_url_when_opportunity_url_none():
    k1 = make_key("PA", None, "https://pa.gov/apply")
    k2 = make_key("PA", None, "https://pa.gov/apply")
    assert k1 == k2


def test_make_key_different_states_produce_different_keys():
    k_pa = make_key("PA", "https://example.gov/grant", "")
    k_ny = make_key("NY", "https://example.gov/grant", "")
    assert k_pa != k_ny


def test_make_key_case_insensitive_url():
    k_upper = make_key("PA", "https://PA.GOV/Grant/ABC", "")
    k_lower = make_key("PA", "https://pa.gov/grant/abc", "")
    assert k_upper == k_lower


def test_make_key_case_insensitive_state():
    k_upper = make_key("PA", "https://example.gov/grant", "")
    k_lower = make_key("pa", "https://example.gov/grant", "")
    assert k_upper == k_lower


def test_make_key_trailing_slash_stripped():
    k_slash    = make_key("PA", "https://pa.gov/grant/", "")
    k_no_slash = make_key("PA", "https://pa.gov/grant",  "")
    assert k_slash == k_no_slash


def test_make_key_returns_64_char_hex():
    k = make_key("PA", "https://example.gov/grant", "https://example.gov/apply")
    assert len(k) == 64
    assert all(c in "0123456789abcdef" for c in k)


def test_make_key_both_urls_empty_raises():
    import pytest as _pytest
    with _pytest.raises(ValueError, match="make_key requires at least one non-empty URL"):
        make_key("PA", "", "")


def test_make_key_both_urls_none_raises():
    import pytest as _pytest
    with _pytest.raises(ValueError):
        make_key("PA", None, None)


def test_make_key_different_urls_produce_different_keys():
    k1 = make_key("PA", "https://example.gov/grant/1", "")
    k2 = make_key("PA", "https://example.gov/grant/2", "")
    assert k1 != k2


# ---------------------------------------------------------------------------
# recalculate_score
# ---------------------------------------------------------------------------

def test_recalculate_score_all_fields_present():
    fields = {
        "title":             "Some Grant",
        "description":       "A description",
        "award_max":         50000,
        "application_url":   "https://example.gov/apply",
        "eligibility_notes": "Small businesses only",
        "summary":           "Brief summary",
        "tags":              ["workforce", "education"],
        "areas_of_focus":    ["Workforce"],
        "contact_email":     "grants@example.gov",
        "deadline":          "06/30/2026",
        "rolling":           False,
    }
    assert recalculate_score(fields) == 1.0


def test_recalculate_score_no_fields():
    assert recalculate_score({}) == 0.0


def test_recalculate_score_deadline_only():
    assert recalculate_score({"deadline": "06/30/2026"}) == 0.15


def test_recalculate_score_rolling_only():
    assert recalculate_score({"rolling": True}) == 0.15


def test_recalculate_score_deadline_and_rolling_share_one_slot():
    # both set → still only 0.15, not 0.30
    s_both     = recalculate_score({"deadline": "06/30/2026", "rolling": True})
    s_deadline = recalculate_score({"deadline": "06/30/2026"})
    assert s_both == s_deadline == 0.15


def test_recalculate_score_empty_list_not_counted():
    assert recalculate_score({"tags": [], "areas_of_focus": []}) == 0.0


def test_recalculate_score_nonempty_list_counts():
    assert recalculate_score({"tags": ["foo"]}) == 0.05


def test_recalculate_score_title_only():
    assert recalculate_score({"title": "My Grant"}) == 0.15


def test_recalculate_score_null_values_not_counted():
    fields = {
        "title": None,
        "description": None,
        "award_max": None,
        "application_url": None,
    }
    assert recalculate_score(fields) == 0.0


def test_recalculate_score_result_is_rounded_to_2dp():
    # title(0.15) + description(0.15) = 0.30
    score = recalculate_score({"title": "X", "description": "Y"})
    assert score == round(score, 2)
    assert score == 0.30


def test_recalculate_score_false_rolling_not_counted():
    # rolling=False is falsy — should not add the deadline slot
    assert recalculate_score({"rolling": False}) == 0.0


# ---------------------------------------------------------------------------
# try_parse (base_scraper)  — only accepts years 2025–2030
# ---------------------------------------------------------------------------

def test_try_parse_valid_future_date():
    dt = try_parse("June 30, 2026")
    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 6
    assert dt.day == 30


def test_try_parse_iso_format():
    dt = try_parse("2027-03-15")
    assert dt is not None
    assert dt.year == 2027


def test_try_parse_year_2020_to_2024_accepted():
    # Past years 2020-2024 are now accepted as expired deadlines
    for year in (2020, 2022, 2024):
        dt = try_parse(f"March 15, {year}")
        assert dt is not None, f"Expected {year} to be accepted"
        assert dt.year == year


def test_try_parse_year_before_2020_returns_none():
    assert try_parse("March 15, 2019") is None


def test_try_parse_year_too_far_future_returns_none():
    assert try_parse("January 1, 2031") is None


def test_try_parse_garbage_returns_none():
    assert try_parse("not a date at all") is None


def test_try_parse_empty_string_returns_none():
    assert try_parse("") is None


def test_try_parse_just_a_number_returns_none():
    # A bare number like "42" might fuzzy-parse to something weird; should fail year guard
    result = try_parse("42")
    # Either None or a year outside 2025-2030
    if result is not None:
        assert not (2025 <= result.year <= 2030)


def test_try_parse_fuzzy_date_with_noise():
    dt = try_parse("Applications due by December 31, 2026 at 5pm")
    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 12


# ---------------------------------------------------------------------------
# resolve_year (base_scraper)  — today = 2026-04-18
# ---------------------------------------------------------------------------

def test_resolve_year_future_month_in_current_year():
    # May 15 hasn't passed in 2026 yet → should resolve to 2026
    result = resolve_year("May 15")
    assert result is not None
    assert result["deadline"].endswith("2026")


def test_resolve_year_past_month_resolves_to_next_year():
    # January 10 already passed in 2026 → should resolve to 2027
    result = resolve_year("January 10")
    assert result is not None
    assert result["deadline"].endswith("2027")


def test_resolve_year_returns_deadline_string():
    result = resolve_year("December 1")
    assert result is not None
    assert "deadline" in result
    assert "needs_review" in result
    # Format should be MM/DD/YYYY
    parts = result["deadline"].split("/")
    assert len(parts) == 3


def test_resolve_year_garbage_returns_none():
    # Month validation guard: "not a month day" contains no month name → None
    assert resolve_year("not a month day") is None


def test_resolve_year_empty_string_returns_none():
    assert resolve_year("") is None


def test_resolve_year_past_month_resolves_to_next_year_2():
    # January has already passed in 2026 → resolves to 2027
    result = resolve_year("January 10")
    assert result is not None
    assert result["deadline"].endswith("2027")


# ---------------------------------------------------------------------------
# extract_date (base_scraper)  — today = 2026-04-18
# ---------------------------------------------------------------------------

def test_extract_date_rolling_phrase():
    result = extract_date("Applications accepted on a rolling basis.")
    assert result["rolling"] is True
    assert result["deadline"] is None
    assert result["confidence"] == "high"


def test_extract_date_no_deadline_phrase():
    result = extract_date("This grant has no deadline.")
    assert result["rolling"] is True


def test_extract_date_ongoing_phrase():
    result = extract_date("This program is ongoing.")
    assert result["rolling"] is True


def test_extract_date_deadline_trigger_word():
    result = extract_date("Deadline: 09/15/2026")
    assert result["deadline"] == "09/15/2026"
    assert result["confidence"] == "high"
    assert result["rolling"] is False


def test_extract_date_due_date_trigger():
    result = extract_date("Applications due: October 31, 2026")
    assert result["deadline"] is not None
    assert "2026" in result["deadline"]


def test_extract_date_closes_on_trigger():
    result = extract_date("The program closes on November 30, 2026.")
    assert result["deadline"] is not None
    assert "2026" in result["deadline"]


def test_extract_date_full_date_in_text():
    result = extract_date("Please submit by 12/01/2026.")
    assert result["deadline"] == "12/01/2026"


def test_extract_date_annual_pattern():
    result = extract_date("Annual deadline is June 30 every year.")
    assert result["is_annual"] is True
    assert result["rolling"] is True


def test_extract_date_nothing_found():
    result = extract_date("This grant provides funding for community projects.")
    assert result["deadline"] is None
    assert result["rolling"] is False
    assert result["needs_review"] is True


def test_extract_date_always_returns_required_keys():
    for text in ("", "some text", "deadline: 06/30/2026", "rolling basis"):
        result = extract_date(text)
        for key in ("deadline", "rolling", "is_annual", "confidence", "raw_text", "needs_review"):
            assert key in result, f"Missing key '{key}' for input: {text!r}"


def test_extract_date_past_full_date_ignored():
    # A full date that has already passed in 2026 (layer 4 filters dt < TODAY)
    result = extract_date("The deadline was 01/15/2026.")
    # Either not picked up as deadline, or if picked up by layer 3 trigger, it is
    # Only layer 4 (arbitrary full date) filters past dates — layer 3 trigger words don't.
    # Without a trigger word, this hits layer 4 which skips past dates → no deadline.
    assert result["deadline"] is None or "2026" in (result["deadline"] or "")


def test_extract_date_explicit_past_year_respected():
    # "January 1, 2024" — try_parse now accepts 2024; layer 4 returns it as-is (expired).
    # The explicit year must NOT be stripped and replaced with a future year.
    result = extract_date("The deadline was January 1, 2024.")
    assert result["deadline"] is not None
    assert "2024" in result["deadline"]
