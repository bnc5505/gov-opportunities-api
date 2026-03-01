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

# ── Path setup ──────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent   # pipeline/../ = project root
APP_DIR      = PROJECT_ROOT / "app"
DATA_ROOT    = PROJECT_ROOT / "data"

# Directories that contain *_raw.json scraper output files (one per state)
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
# Change CWD so SQLite URL `sqlite:///./gov_grants.db` resolves to app/gov_grants.db
os.chdir(str(APP_DIR))

# ── SQLAlchemy imports (after path is set) ───────────────────────────────────
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, Text, DateTime, text
)
from sqlalchemy.orm import declarative_base
import database  # our existing database.py (engine + SessionLocal)

# ── Model ────────────────────────────────────────────────────────────────────
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


# ── Config ───────────────────────────────────────────────────────────────────

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

            # Determine state from filename prefix
            state = ""
            for prefix, code in PREFIX_STATE_MAP.items():
                if filepath.name.lower().startswith(prefix):
                    state = code
                    break

            found.append((filepath, state))

    return found


# Nav-link / page-title patterns that the scraper mistakenly captured as grants
JUNK_TITLES = {
    "Library", "Why PA", "Demographics", "Mapping", "Compare Communities",
    "Grants and Funding", "OVSJG Funding Sources", "Funding Recipients",
    "Grant Management Resources for Grantees", "Apply to Be a Peer Reviewer",
    "Training and Technical Assistance", "Current Funding Opportunities",
    "Search for Funding", "How to Apply",
}

# Statuses we want to keep
KEEP_STATUSES = {"active", "rolling", "expiring_soon", "recently_closed", "unverified"}

# Award amounts above this threshold are scraper artefacts (grabbed page stats)
AWARD_MAX_CAP = 2_000_000_000

MIN_QUALITY_SCORE = 0.2


# ── Helpers ──────────────────────────────────────────────────────────────────

def normalize_deadline(dl):
    """Ensure deadline is MM/DD/YYYY; return None if unparseable."""
    if not dl:
        return None
    dl = dl.strip()
    # Already MM/DD/YYYY
    if re.fullmatch(r"\d{2}/\d{2}/\d{4}", dl):
        return dl
    # Try common alternative formats
    for fmt in ("%Y-%m-%d", "%m-%d-%Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(dl, fmt).strftime("%m/%d/%Y")
        except ValueError:
            pass
    return None  # unparseable – store as None


def cap_award(value):
    if value is None:
        return None
    return None if value > AWARD_MAX_CAP else value


def json_list(value) -> str:
    """Serialize a list (or None) to a JSON string for storage."""
    if isinstance(value, list):
        return json.dumps(value)
    return json.dumps([])


def is_junk(grant: dict) -> bool:
    """Return True if this grant record looks like a nav link or placeholder."""
    title = (grant.get("title") or "").strip()
    if title in JUNK_TITLES:
        return True
    # Very short titles (1-2 words) with no URL at all are likely nav items
    if len(title.split()) <= 2 and not grant.get("opportunity_url") and not grant.get("description"):
        return True
    return False


def dedup_key(grant: dict) -> str:
    """
    Build a deduplication key.  Prefer application_url; fall back to
    lower-cased title + state so we catch near-duplicates across files.
    """
    url = (grant.get("application_url") or "").strip()
    if url:
        return url.lower()
    title = (grant.get("title") or "").strip().lower()
    state = (grant.get("state") or "").upper()
    return f"{state}::{title}"


# ── Main ─────────────────────────────────────────────────────────────────────

def load_grants():
    # Create the new table (no-op if it already exists)
    Base.metadata.create_all(bind=database.engine)

    all_raw: list[tuple[dict, str]] = []   # (grant_dict, source_filename)

    # ── 1. Read all JSON files ───────────────────────────────────────────────
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
            # Use state from JSON envelope if present, else fall back to prefix map
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

    # ── 2. Clean & filter ────────────────────────────────────────────────────
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

        # c) Status filter – drop archived with no deadline + no description
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

    # ── 3. Load into database ────────────────────────────────────────────────
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
                    deadline           = normalize_deadline(g.get("deadline")),
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

    # ── 4. Summary report ────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"LOAD COMPLETE  — {loaded} grants inserted, {load_errors} skipped")
    print(f"{'='*60}")

    # Reload for reporting
    db = database.SessionLocal()
    try:
        rows = db.query(ScrapedGrant).all()

        # Per-state counts
        state_counts = Counter(r.state for r in rows)
        print("\nGrants per state:")
        for state in sorted(state_counts):
            print(f"  {state}: {state_counts[state]}")

        # Active vs needs_review
        active_count  = sum(1 for r in rows if r.status in ("active", "rolling", "expiring_soon"))
        review_count  = sum(1 for r in rows if r.needs_review)
        print(f"\nActive / rolling / expiring_soon: {active_count}")
        print(f"Flagged needs_review:             {review_count}")

        # Top 10 by data_quality_score
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
