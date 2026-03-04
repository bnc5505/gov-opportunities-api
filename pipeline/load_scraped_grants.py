#!/usr/bin/env python3
"""
load_scraped_grants.py

Reads raw scraper JSON output, cleans and deduplicates the data, creates
the `scraped_grants` table if needed, and bulk-loads into it.

Automatically discovers *_raw.json files across state-organized data dirs:
  - data/dc/  - data/md/  - data/ny/  - data/pa/

Run from the project root:
    python pipeline/load_scraped_grants.py
"""

import sys
import os
import json
import re
from datetime import datetime
from pathlib import Path
from collections import Counter

PROJECT_ROOT = Path(__file__).resolve().parent.parent   # pipeline/../ = project root
APP_DIR      = PROJECT_ROOT / "app"
DATA_ROOT    = PROJECT_ROOT / "data"

SCRAPER_DIRS = [
    DATA_ROOT / "dc",
    DATA_ROOT / "md",
    DATA_ROOT / "ny",
    DATA_ROOT / "pa",
]

# Map filename prefix → state code (for files that don't embed state in JSON)
PREFIX_STATE_MAP = {
    "pa_": "PA",
    "dc_": "DC",
    "md_": "MD",
    "ny_": "NY",
}

sys.path.insert(0, str(APP_DIR))
# CWD must be app/ so SQLite resolves to app/gov_grants.db
os.chdir(str(APP_DIR))

from sqlalchemy import (
    Column, Integer, String, Float, Boolean, Text, DateTime, text
)
from sqlalchemy.orm import declarative_base
import database  # our existing database.py (engine + SessionLocal)

Base = declarative_base()

class ScrapedGrant(Base):
    __tablename__ = "scraped_grants"

    id                 = Column(Integer, primary_key=True, autoincrement=True)
    title              = Column(String(500), nullable=False)
    state              = Column(String(10))
    status             = Column(String(50))
    deadline           = Column(String(20))   # MM/DD/YYYY or None
    rolling            = Column(Boolean, default=False)
    is_annual          = Column(Boolean, default=False)
    award_min          = Column(Float,   nullable=True)
    award_max          = Column(Float,   nullable=True)
    total_funding      = Column(Float,   nullable=True)
    award_text         = Column(String(500))
    description        = Column(Text)
    summary            = Column(Text)
    eligibility_notes  = Column(Text)
    contact_email      = Column(String(200))
    contact_name       = Column(String(200))
    application_url    = Column(String(1000))
    opportunity_url    = Column(String(1000))
    tags               = Column(Text)          # JSON array stored as text
    areas_of_focus     = Column(Text)          # JSON array stored as text
    industry           = Column(String(200))
    data_quality_score = Column(Float)
    needs_review       = Column(Boolean, default=True)
    source_file        = Column(String(200))
    loaded_at          = Column(DateTime, default=datetime.utcnow)


def discover_source_files() -> list[tuple[Path, str]]:
    """
    Auto-discover all *_raw.json files across all SCRAPER_DIRS.
    Returns a list of (filepath, state_code) tuples.
    State is read from the JSON 'state' key first; falls back to filename prefix.
    """
    found: list[tuple[Path, str]] = []
    seen_names: set[str] = set()   # avoid loading same filename from two dirs

    for scraper_dir in SCRAPER_DIRS:
        if not scraper_dir.exists():
            continue
        for filepath in sorted(scraper_dir.glob("*_raw.json")):
            if filepath.name in seen_names:
                continue
            seen_names.add(filepath.name)

            state = ""
            for prefix, code in PREFIX_STATE_MAP.items():
                if filepath.name.lower().startswith(prefix):
                    state = code
                    break

            found.append((filepath, state))

    return found


# Known junk titles (nav items, non-grant pages)
JUNK_TITLES = {
    "Library", "Services", "Agency Directory", "The Governor", "Lt. Governor",
    "Programs and Services", "About", "Contact Us", "Home", "Sitemap",
    "Grants", "Grants and Funding", "Funding", "Budget",
    "Boards & Commissions", "Boards and Commissions", "Bureau Directors",
    "Career Exploration", "Careers & Internships", "Careers and Internships",
    "OVSJG Funding Sources", "Funding Recipients", "Address Confidentiality Program",
    "Language Access Program",
    "Grant Management Resources for Grantees", "Apply to Be a Peer Reviewer",
    "Training and Technical Assistance", "Current Funding Opportunities",
    "Search for Funding", "How to Apply",
    "Animals", "Plants", "Food", "Budget", "Business and Industry",
    "Agricultural Marketing", "Agricultural Trade", "Agricultural Security Areas",
    "Agronomic Products", "Biosecurity", "Brucellosis", "Avian Influenza",
    "Chronic Wasting Disease", "Beginning Farmers",
    "Amusement Rides and Attractions",
    "Why PA", "Demographics", "Mapping", "Compare Communities",
}

# Regex patterns on the title — if matched → junk
JUNK_TITLE_PATTERNS = [
    re.compile(r"^about\s+(pda|the\s|our\s)", re.I),
    re.compile(r"\badvisory\s+board\b.*minutes", re.I),
    re.compile(r"\bboard\s+meeting\s+minutes\b", re.I),
    re.compile(r"\bmeeting\s+minutes\b", re.I),
    re.compile(r"^bureau\s+of\s+\w", re.I),
    re.compile(r"^bureau\s+directors?\b", re.I),
    re.compile(r"^department\s+of\s+labor\s+and\s+industry$", re.I),
    re.compile(r"^employment\s+and\s+workforce\s+development$", re.I),
    re.compile(r"^workforce\s+development\s+resources?$", re.I),
    re.compile(r"^pa\s+careerlink", re.I),
    re.compile(r"\blicens(e|ing)\s+(application|program|requirement)", re.I),
    re.compile(r"^apply\s+for\s+a\s+\w+\s+(license|permit)\b", re.I),
    re.compile(r"^become\s+an?\s+\w+\s+vendor$", re.I),
    re.compile(r"^certified\s+\w+\s+(agent|inspector|program)$", re.I),
    re.compile(r"exhibition\s+and\s+movement\s+rules?", re.I),
    re.compile(r"\bhealth\s+commission\b", re.I),
    re.compile(r"^(lt\.?|lieutenant)\s+governor\b", re.I),
    re.compile(r"^(the\s+)?governor\b", re.I),
]

# URL path segments that indicate a non-grant page
JUNK_URL_PATH_SEGMENTS = [
    "/about-pda", "/about-the-", "/about-us",
    "/agency-directory", "/agencies$", "/agencies/",
    "/bureau-directors",
    "/boards-commissions", "/boards_commissions",
    "/animals/diseases/", "/animals/bureau",
    "/consumer-protection/amusement",
    "/open-jobs", "/careers-", "/budget",
    "/governor", "/ltgovernor",
    "/programs-services",
    "/workforce-development-home",
    "/americorps-in-pennsylvania",
    "/pa-careerlink",
    "/financial-aid",
    "/grant-opportunities",
    "/home----------",
    "/resources",
]

# Title must contain at least one of these to be considered a real grant opportunity
GRANT_SIGNAL_WORDS = {
    "grant", "fund", "loan", "award", "assistance", "incentive",
    "scholarship", "rebate", "reimbursement", "credit", "subsidy",
    "subsidi", "relief", "program", "initiative", "opportunity",
    "conservation", "preservation", "restoration", "revitalization",
    "improvement", "enhancement", "expansion", "workforce", "housing",
    "infrastructure", "environmental", "community",
}

KEEP_STATUSES = {"active", "rolling", "expiring_soon", "recently_closed", "unverified"}

# Values above this are almost certainly scraper artifacts (e.g. grabbed a page view count)
AWARD_MAX_CAP = 2_000_000_000

MIN_QUALITY_SCORE = 0.2

_TODAY_STR = datetime.utcnow().strftime("%m/%d/%Y")


def normalize_deadline(dl):
    """Ensure deadline is MM/DD/YYYY; return None if unparseable."""
    if not dl:
        return None
    dl = dl.strip()
    if re.fullmatch(r"\d{2}/\d{2}/\d{4}", dl):
        return dl
    for fmt in ("%Y-%m-%d", "%m-%d-%Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(dl, fmt).strftime("%m/%d/%Y")
        except ValueError:
            pass
    return None


def cap_award(value):
    if value is None:
        return None
    return None if value > AWARD_MAX_CAP else value


def json_list(value) -> str:
    """Serialize a list (or None) to a JSON string for storage."""
    if isinstance(value, list):
        return json.dumps(value)
    return json.dumps([])


def _url_is_nav(url: str) -> bool:
    """Return True if the opportunity URL looks like a nav/info page, not a grant."""
    if not url:
        return False
    path = url.lower().split("?")[0]
    for seg in JUNK_URL_PATH_SEGMENTS:
        if seg in path:
            return True
    return False


def _title_has_grant_signal(title: str) -> bool:
    """Return True if at least one grant-signal word appears in the title."""
    title_lower = title.lower()
    return any(word in title_lower for word in GRANT_SIGNAL_WORDS)


def is_junk(grant: dict) -> bool:
    """
    Return True if this grant record looks like a nav link, regulatory page,
    or other non-grant content captured by over-broad scraper link collection.

    Checks applied in order (fast → expensive):
      1. Exact title match against known junk titles
      2. Regex pattern match against junk title patterns
      3. URL path matches known non-grant path segments
      4. No grant-signal word in title AND no AI-enriched content
      5. Very short title with no supporting data
    """
    title   = (grant.get("title") or "").strip()
    opp_url = (grant.get("opportunity_url") or "").strip()
    desc    = (grant.get("description") or "").strip()
    award   = grant.get("award_max") or grant.get("award_min") or grant.get("award_text")

    if title in JUNK_TITLES:
        return True

    for pat in JUNK_TITLE_PATTERNS:
        if pat.search(title):
            return True

    if _url_is_nav(opp_url) and not desc and not award:
        return True

    if not _title_has_grant_signal(title) and not desc and not award:
        return True

    if len(title.split()) <= 2 and not opp_url and not desc:
        return True

    return False


def _sanitize_deadline(dl, grant: dict):
    """
    Strip false-positive deadlines that are exactly today's date —
    a common artifact when the scraper picks up the page's last-updated
    or copyright footer date instead of a real application deadline.
    Only clear it when no other funding data is present (score ≤ 0.35).
    """
    if not dl:
        return None
    normalized = normalize_deadline(dl)
    score = grant.get("data_quality_score") or 0
    if normalized == _TODAY_STR and score <= 0.35:
        return None
    return normalized


def dedup_key(grant: dict) -> str:
    """
    Build a deduplication key. Prefer opportunity_url (unique source page);
    fall back to title + state.
    application_url is intentionally NOT used — multiple grants often share
    a single generic apply link (e.g. MSDE grants all link to the same portal).
    """
    opp_url = (grant.get("opportunity_url") or "").strip()
    if opp_url:
        return opp_url.lower()
    title = (grant.get("title") or "").strip().lower()
    state = (grant.get("state") or "").upper()
    return f"{state}::{title}"


def load_grants():
    Base.metadata.create_all(bind=database.engine)

    all_raw: list[tuple[dict, str]] = []   # (grant_dict, source_filename)

    # 1. Read all JSON files
    source_files = discover_source_files()
    if not source_files:
        print("  [WARN] No *_raw.json files found in any scraper directory")
    else:
        print(f"  Discovered {len(source_files)} source file(s):")

    for filepath, default_state in source_files:
        if not filepath.exists():
            print(f"  [SKIP] {filepath.name} not found")
            continue
        try:
            with open(filepath, encoding="utf-8") as fh:
                data = json.load(fh)
            grants = data.get("grants", [])
            file_state = data.get("state") or default_state
            for g in grants:
                if not g.get("state"):
                    g["state"] = file_state
                all_raw.append((g, filepath.name))
            print(f"  [READ] {filepath.name}: {len(grants)} grants  [{file_state}]")
        except Exception as exc:
            print(f"  [ERROR] reading {filepath.name}: {exc}")

    total_raw = len(all_raw)
    print(f"\nTotal raw records: {total_raw}")

    # 2. Clean and filter
    seen_keys:   set[str]  = set()
    removed_junk:      int = 0
    removed_quality:   int = 0
    removed_status:    int = 0
    removed_archived:  int = 0
    removed_dupes:     int = 0
    cleaned: list[tuple[dict, str]] = []

    for g, source_file in all_raw:
        # a) Junk title check
        if is_junk(g):
            removed_junk += 1
            continue

        # b) Quality score filter
        score = g.get("data_quality_score") or 0.0
        if score < MIN_QUALITY_SCORE:
            removed_quality += 1
            continue

        # c) Status filter — drop archived with no deadline + no description
        status = (g.get("status") or "").lower()
        if status not in KEEP_STATUSES:
            if status == "archived" and not g.get("deadline") and not g.get("description"):
                removed_archived += 1
                continue
            elif status not in KEEP_STATUSES:
                removed_status += 1
                continue

        # d) Deduplication
        key = dedup_key(g)
        if key in seen_keys:
            removed_dupes += 1
            continue
        seen_keys.add(key)

        cleaned.append((g, source_file))

    print(f"\nRemoved junk titles:    {removed_junk}")
    print(f"Removed low quality:    {removed_quality}")
    print(f"Removed bad status:     {removed_status}")
    print(f"Removed archived empty: {removed_archived}")
    print(f"Removed duplicates:     {removed_dupes}")
    print(f"Grants after cleaning:  {len(cleaned)}")

    # 3. Load into database
    db = database.SessionLocal()
    loaded      = 0
    load_errors = 0

    try:
        # Wipe any previous load so this script is idempotent
        existing = db.execute(text("SELECT COUNT(*) FROM scraped_grants")).scalar()
        if existing:
            print(f"\nClearing {existing} existing rows from scraped_grants …")
            db.execute(text("DELETE FROM scraped_grants"))
            db.commit()

        for g, source_file in cleaned:
            try:
                row = ScrapedGrant(
                    title              = (g.get("title") or "").strip()[:500],
                    state              = (g.get("state") or "").upper(),
                    status             = (g.get("status") or "").lower(),
                    deadline           = _sanitize_deadline(g.get("deadline"), g),
                    rolling            = bool(g.get("rolling", False)),
                    is_annual          = bool(g.get("is_annual", False)),
                    award_min          = cap_award(g.get("award_min")),
                    award_max          = cap_award(g.get("award_max")),
                    total_funding      = cap_award(g.get("total_funding")),
                    award_text         = (g.get("award_text") or "")[:500] or None,
                    description        = g.get("description"),
                    summary            = g.get("summary"),
                    eligibility_notes  = g.get("eligibility_notes"),
                    contact_email      = (g.get("contact_email") or "")[:200] or None,
                    contact_name       = (g.get("contact_name")  or "")[:200] or None,
                    application_url    = (g.get("application_url") or "")[:1000] or None,
                    opportunity_url    = (g.get("opportunity_url") or "")[:1000] or None,
                    tags               = json_list(g.get("tags")),
                    areas_of_focus     = json_list(g.get("areas_of_focus")),
                    industry           = (g.get("industry") or "")[:200] or None,
                    data_quality_score = g.get("data_quality_score"),
                    needs_review       = bool(g.get("needs_review", True)),
                    source_file        = source_file,
                    loaded_at          = datetime.utcnow(),
                )
                db.add(row)
                db.flush()
                loaded += 1
            except Exception as exc:
                db.rollback()
                load_errors += 1
                print(f"  [SKIP ROW] {g.get('title', '?')!r}: {exc}")

        db.commit()
    except Exception as exc:
        db.rollback()
        print(f"\n[FATAL] {exc}")
        raise
    finally:
        db.close()

    print(f"\n{'='*60}")
    print(f"LOAD COMPLETE  — {loaded} grants inserted, {load_errors} skipped")
    print(f"{'='*60}")

    db = database.SessionLocal()
    try:
        rows = db.query(ScrapedGrant).all()

        state_counts = Counter(r.state for r in rows)
        print("\nGrants per state:")
        for state in sorted(state_counts):
            print(f"  {state}: {state_counts[state]}")

        active_count  = sum(1 for r in rows if r.status in ("active", "rolling", "expiring_soon"))
        review_count  = sum(1 for r in rows if r.needs_review)
        print(f"\nActive / rolling / expiring_soon: {active_count}")
        print(f"Flagged needs_review:             {review_count}")

        top10 = sorted(rows, key=lambda r: (r.data_quality_score or 0), reverse=True)[:10]
        print("\nTop 10 by data_quality_score:")
        print(f"  {'Score':>6}  {'State':>5}  Title")
        print(f"  {'-'*6}  {'-'*5}  {'-'*50}")
        for r in top10:
            score_str = f"{r.data_quality_score:.2f}" if r.data_quality_score else "  —  "
            title_str = r.title[:55] if r.title else "(no title)"
            print(f"  {score_str:>6}  {r.state:>5}  {title_str}")

    finally:
        db.close()


if __name__ == "__main__":
    print("="*60)
    print("load_scraped_grants.py — starting")
    print("="*60)
    load_grants()
