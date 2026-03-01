#!/usr/bin/env python3
"""
sync_opportunities.py

Reads live-ready rows from scraped_grants and upserts them into the
opportunities table using opportunity_key as the dedup anchor.

Live-ready criteria:
  - title present
  - application_url present
  - deadline IS NOT NULL OR rolling = 1
  - data_quality_score >= MIN_SCORE (default 0.4)
  - status in (active, rolling, expiring_soon, recently_closed, unverified)

Upsert:
  - opportunity_key = sha256(state_code + "|" + application_url)
  - If key exists → UPDATE + last_synced_at
  - If new        → INSERT

Review queue:
  - data_quality_score < 0.6 → queued for review
  - needs_review = True      → queued for review
  - Existing PENDING entries are not duplicated

Run from project root:
    python sync_opportunities.py [--dry-run] [--min-score 0.4]
"""

import sys
import os
import json
import hashlib
import argparse
import logging
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent   # pipeline/../ = project root
APP_DIR      = PROJECT_ROOT / "app"

sys.path.insert(0, str(APP_DIR))
os.chdir(str(APP_DIR))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

import database
from sqlalchemy import text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

MIN_SCORE    = 0.40
REVIEW_BELOW = 0.60

LIVE_STATUSES = {"active", "rolling", "expiring_soon", "recently_closed", "unverified"}

STATUS_MAP = {
    "active":          "active",
    "rolling":         "active",
    "expiring_soon":   "active",
    "recently_closed": "expired",
    "unverified":      "unverified",
}

SOURCE_NAME_MAP = {
    "pa_dced_grants_raw.json":     "PA DCED Programs",
    "dc_ovsjg_grants_raw.json":    "DC OVSJG Grants",
    "dc_dslbd_grants_raw.json":    "DC Small Business Grants",
    "md_commerce_grants_raw.json": "Maryland Commerce Funding",
    "md_bworks_grants_raw.json":   "Maryland Business Works",
    "md_dhcd_grants_raw.json":     "Maryland DHCD Housing",
    "md_grants_portal_raw.json":   "Maryland Grants Portal",
    "ny_esd_grants_raw.json":      "NY ESD Grants",
    "ny_grants_gateway_raw.json":  "NY Grants Gateway",
    "ny_nyserda_grants_raw.json":  "NY NYSERDA",
    "ny_gov_grants_raw.json":      "NY Gov Grants",
}
SOURCE_URLS = {
    "pa_dced_grants_raw.json":     "https://dced.pa.gov/programs/",
    "dc_ovsjg_grants_raw.json":    "https://ovsjg.dc.gov/page/funding-opportunities-current",
    "dc_dslbd_grants_raw.json":    "https://dslbd.dc.gov/",
    "md_commerce_grants_raw.json": "https://commerce.maryland.gov/fund",
    "md_bworks_grants_raw.json":   "https://bworks.maryland.gov/",
    "md_dhcd_grants_raw.json":     "https://dhcd.maryland.gov/",
    "md_grants_portal_raw.json":   "https://grants.maryland.gov/",
    "ny_esd_grants_raw.json":      "https://esd.ny.gov/",
    "ny_grants_gateway_raw.json":  "https://grantsgateway.ny.gov/",
    "ny_nyserda_grants_raw.json":  "https://www.nyserda.ny.gov/",
    "ny_gov_grants_raw.json":      "https://www.grants.ny.gov/",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_key(state_code: str, application_url: str) -> str:
    raw = f"{state_code.lower()}|{(application_url or '').lower().rstrip('/')}"
    return hashlib.sha256(raw.encode()).hexdigest()[:64]


def parse_deadline(dl_str):
    if not dl_str:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(dl_str.strip(), fmt).isoformat()
        except ValueError:
            pass
    return None


def parse_json_field(value):
    if not value:
        return None
    if isinstance(value, list):
        return json.dumps(value)
    if isinstance(value, str) and value.startswith("["):
        return value  # already JSON string
    return None


def is_live_ready(row, min_score: float) -> bool:
    if not row["title"] or not row["application_url"]:
        return False
    if (row["data_quality_score"] or 0) < min_score:
        return False
    if (row["status"] or "").lower() not in LIVE_STATUSES:
        return False
    if not row["deadline"] and not row["rolling"]:
        return False
    return True


# ── DB helpers ────────────────────────────────────────────────────────────────

_state_cache  = {}
_source_cache = {}


def get_state_id(conn, code: str):
    if code not in _state_cache:
        row = conn.execute(
            text("SELECT id FROM states WHERE code = :c"), {"c": code.upper()}
        ).fetchone()
        _state_cache[code] = row[0] if row else None
    return _state_cache[code]


def get_or_create_source(conn, source_file: str) -> int:
    if source_file in _source_cache:
        return _source_cache[source_file]
    name = SOURCE_NAME_MAP.get(source_file, source_file)
    url  = SOURCE_URLS.get(source_file, "https://unknown.gov/")
    # Look up by name OR url to avoid unique constraint errors
    row  = conn.execute(
        text("SELECT id FROM sources WHERE name = :n OR url = :u"), {"n": name, "u": url}
    ).fetchone()
    if row:
        _source_cache[source_file] = row[0]
    else:
        code = source_file.split("_")[0].upper()
        sid  = get_state_id(conn, code) if len(code) == 2 else None
        conn.execute(text("""
            INSERT INTO sources (name, url, state_id, scraper_type, scrape_frequency_hours,
                                 is_active)
            VALUES (:name, :url, :sid, 'scraper', 24, 1)
        """), {"name": name, "url": url, "sid": sid})
        row2 = conn.execute(
            text("SELECT id FROM sources WHERE name = :n"), {"n": name}
        ).fetchone()
        _source_cache[source_file] = row2[0]
    return _source_cache[source_file]


# ── Core upsert (raw SQL) ─────────────────────────────────────────────────────

def upsert_opportunity(conn, row, dry_run: bool) -> tuple:
    """Returns (action, opportunity_id, queued)."""
    state_code = (row["state"] or "").upper()
    app_url    = (row["application_url"] or "").strip()
    key        = make_key(state_code, app_url)

    state_id   = get_state_id(conn, state_code)
    source_id  = get_or_create_source(conn, row["source_file"] or "")
    deadline   = parse_deadline(row["deadline"])
    tags_json  = parse_json_field(row["tags"])
    aof_json   = parse_json_field(row["areas_of_focus"])
    status     = STATUS_MAP.get((row["status"] or "").lower(), "unverified")

    existing = conn.execute(
        text("SELECT id FROM opportunities WHERE opportunity_key = :k"), {"k": key}
    ).fetchone()

    if dry_run:
        action = "would_update" if existing else "would_insert"
        return action, existing[0] if existing else None, False

    params = dict(
        key        = key,
        title      = (row["title"] or "").strip()[:500],
        desc       = row["description"],
        summary    = (row["summary"] or "")[:1000] or None,
        otype      = "grant",
        source_id  = source_id,
        state_id   = state_id,
        elig_org   = 1,
        elig_ind   = 0,
        elig_desc  = row["eligibility_notes"],
        award_min  = row["award_min"],
        award_max  = row["award_max"],
        total_f    = row["total_funding"],
        deadline   = deadline,
        rolling    = 1 if row["rolling"] else 0,
        opp_url    = (row["opportunity_url"] or "")[:1000] or None,
        app_url    = app_url[:1000] or None,
        c_name     = (row["contact_name"]  or "")[:255] or None,
        c_email    = (row["contact_email"] or "")[:255] or None,
        tags       = tags_json,
        aof        = aof_json,
        industry   = (row["industry"] or "")[:255] or None,
        status     = status,
        score      = row["data_quality_score"],
        needs_rev  = 1 if row["needs_review"] else 0,
        synced_at  = datetime.utcnow().isoformat(),
    )

    if existing:
        conn.execute(text("""
            UPDATE opportunities SET
                title                   = :title,
                description             = :desc,
                summary                 = :summary,
                opportunity_type        = :otype,
                source_id               = :source_id,
                state_id                = :state_id,
                eligibility_organization= :elig_org,
                eligibility_individual  = :elig_ind,
                eligibility_description = :elig_desc,
                award_min               = :award_min,
                award_max               = :award_max,
                total_funding           = :total_f,
                deadline                = :deadline,
                rolling                 = :rolling,
                opportunity_url         = :opp_url,
                application_url         = :app_url,
                contact_name            = :c_name,
                contact_email           = :c_email,
                tags                    = :tags,
                opportunity_gap_resources = :aof,
                industry                = :industry,
                status                  = :status,
                data_quality_score      = :score,
                needs_review            = :needs_rev,
                last_synced_at          = :synced_at,
                updated_at              = :synced_at
            WHERE opportunity_key = :key
        """), params)
        opp_id = existing[0]
        action = "updated"
    else:
        conn.execute(text("""
            INSERT INTO opportunities (
                opportunity_key, title, description, summary,
                opportunity_type, source_id, state_id,
                eligibility_organization, eligibility_individual, eligibility_description,
                award_min, award_max, total_funding,
                deadline, rolling,
                opportunity_url, application_url,
                contact_name, contact_email,
                tags, opportunity_gap_resources, industry,
                status, data_quality_score, needs_review,
                last_synced_at, created_at, updated_at
            ) VALUES (
                :key, :title, :desc, :summary,
                :otype, :source_id, :state_id,
                :elig_org, :elig_ind, :elig_desc,
                :award_min, :award_max, :total_f,
                :deadline, :rolling,
                :opp_url, :app_url,
                :c_name, :c_email,
                :tags, :aof, :industry,
                :status, :score, :needs_rev,
                :synced_at, :synced_at, :synced_at
            )
        """), params)
        row2   = conn.execute(
            text("SELECT id FROM opportunities WHERE opportunity_key = :k"), {"k": key}
        ).fetchone()
        opp_id = row2[0] if row2 else None
        action = "inserted"

    # Review queue
    queued = False
    score  = row["data_quality_score"] or 0
    needs  = bool(row["needs_review"])
    if opp_id and (score < REVIEW_BELOW or needs):
        existing_q = conn.execute(
            text("SELECT id FROM review_queue WHERE opportunity_id=:oid AND reviewed=0"),
            {"oid": opp_id}
        ).fetchone()
        if not existing_q:
            reasons = []
            if score < REVIEW_BELOW:
                reasons.append(f"low_quality (score={score:.2f})")
            if needs:
                reasons.append("scraper_flagged")
            conn.execute(text("""
                INSERT INTO review_queue (opportunity_id, reason, reviewed, created_at)
                VALUES (:oid, :reason, 0, datetime('now'))
            """), {"oid": opp_id, "reason": "; ".join(reasons)})
            queued = True

    return action, opp_id, queued


# ── Main ──────────────────────────────────────────────────────────────────────

def main(dry_run=False, min_score=MIN_SCORE):
    db = database.SessionLocal()
    conn = db.connection()

    try:
        all_rows = conn.execute(text("SELECT * FROM scraped_grants")).mappings().fetchall()
        live     = [r for r in all_rows if is_live_ready(r, min_score)]
        blocked  = len(all_rows) - len(live)

        log.info(f"Total scraped_grants:  {len(all_rows)}")
        log.info(f"Live-ready to sync:    {len(live)}")
        log.info(f"Blocked (no deadline): {blocked}")
        if dry_run:
            log.info("DRY-RUN — no DB writes")

        stats = {"inserted": 0, "updated": 0, "queued": 0, "errors": 0}

        for row in live:
            try:
                action, opp_id, queued = upsert_opportunity(conn, row, dry_run)
                key = action.replace("would_", "")
                stats[key] = stats.get(key, 0) + 1
                if queued:
                    stats["queued"] += 1
            except Exception as exc:
                stats["errors"] += 1
                log.error(f"  Error on '{(row['title'] or '')[:50]}': {exc}")

        if not dry_run:
            db.commit()
        else:
            db.rollback()

    except Exception as exc:
        db.rollback()
        log.error(f"Fatal: {exc}")
        raise
    finally:
        db.close()

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"SYNC COMPLETE{'  [DRY-RUN]' if dry_run else ''}")
    print(f"{'='*60}")
    print(f"  Inserted:           {stats.get('inserted', 0)}")
    print(f"  Updated:            {stats.get('updated', 0)}")
    print(f"  Errors:             {stats['errors']}")
    print(f"  Queued for review:  {stats['queued']}")
    print(f"  Blocked grants:     {blocked}  (no deadline + not rolling)")

    if not dry_run:
        db2 = database.SessionLocal()
        try:
            total  = db2.execute(text("SELECT COUNT(*) FROM opportunities")).scalar()
            active = db2.execute(text("SELECT COUNT(*) FROM opportunities WHERE status='active'")).scalar()
            pending= db2.execute(text("SELECT COUNT(*) FROM review_queue WHERE reviewed=0")).scalar()
            print(f"\nopportunities table: {total} total, {active} active")
            print(f"review_queue:        {pending} pending review")
            print(f"\nTop 10 by data_quality_score:")
            print(f"  {'Score':>6}  {'Status':>12}  Title")
            print(f"  {'-'*6}  {'-'*12}  {'-'*50}")
            rows = db2.execute(text(
                "SELECT o.data_quality_score, o.status, o.title, s.code "
                "FROM opportunities o LEFT JOIN states s ON o.state_id=s.id "
                "ORDER BY o.data_quality_score DESC LIMIT 10"
            )).fetchall()
            for score, st, title, sc in rows:
                print(f"  {score or 0:>6.2f}  {st or '?':>12}  [{sc or '?'}] {(title or '')[:50]}")
        finally:
            db2.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync live-ready scraped_grants → opportunities")
    parser.add_argument("--dry-run",   action="store_true")
    parser.add_argument("--min-score", type=float, default=MIN_SCORE)
    args = parser.parse_args()
    main(dry_run=args.dry_run, min_score=args.min_score)
