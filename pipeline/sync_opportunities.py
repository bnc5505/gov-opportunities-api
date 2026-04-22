#!/usr/bin/env python3

"""
sync_opportunities.py

Reads live-ready rows from scraped_grants and upserts them into the
opportunities table using opportunity_key as the dedup anchor.

Live-ready criteria:
- title present
- application_url present
- deadline IS NOT NULL OR rolling = TRUE
- data_quality_score >= MIN_SCORE (default 0.4)
- status in (active, rolling, expiring_soon, recently_closed, unverified)

Upsert:
- opportunity_key = sha256(state_code + "|" + opportunity_url)  [fallback: application_url]
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
import re
import json
import hashlib
import argparse
import logging
from datetime import datetime, date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent   # pipeline/../ = project root
APP_DIR      = PROJECT_ROOT / "app"

sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(str(APP_DIR))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

import database
from sqlalchemy import text
from pipeline.constants import (
    MIN_SCORE,
    HIGH_SCORE,
    REVIEW_BELOW,
    MIN_AWARD_THRESHOLD,
)
from pipeline.agency_logos import get_logo_url as _get_logo_url


def _ensure_opportunities_columns():
    """Add new columns to the opportunities table if not already present.
    Each ALTER TABLE runs in its own connection so a 'column already exists'
    error never poisons any other transaction.
    """
    for col, defn in [
        ("logo_url", "VARCHAR(500)"),
    ]:
        with database.engine.connect() as conn:
            try:
                conn.execute(text(f"ALTER TABLE opportunities ADD COLUMN {col} {defn}"))
                conn.commit()
            except Exception:
                conn.rollback()  # column already exists — fine

def expire_stale_grants() -> int:
    """
    Update opportunities to status='expired' where deadline has passed and rolling is not true.
    Uses today's local date for comparison. Returns the count of rows updated.
    """
    today = date.today().isoformat()
    with database.engine.connect() as conn:
        result = conn.execute(text("""
            UPDATE opportunities
            SET status = 'expired', updated_at = :now
            WHERE status = 'active'
              AND deadline IS NOT NULL
              AND deadline < :today
              AND (rolling IS NULL OR rolling = FALSE)
        """), {"today": today, "now": datetime.utcnow().isoformat()})
        conn.commit()
        return result.rowcount


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# MIN_SCORE, HIGH_SCORE, REVIEW_BELOW imported from pipeline.constants
MIN_AWARD = MIN_AWARD_THRESHOLD  # local alias — keeps is_live_ready readable

# only push grants that are currently open or rolling
LIVE_STATUSES = {"active", "rolling", "expiring_soon"}

STATUS_MAP = {
    "active":          "active",
    "rolling":         "active",
    "expiring_soon":   "active",
    "recently_closed": "expired",
    "unverified":      "unverified",
}

SOURCE_NAME_MAP = {
    # Pennsylvania
    "pa_dced_grants_raw.json":        "PA DCED Programs",
    "pa_gov_grants_raw.json":         "PA Official Grants Directory",
    "pa_dli_grants_raw.json":         "PA Dept of Labor & Industry",
    "pa_dcnr_grants_raw.json":        "PA DCNR Conservation Grants",
    "pa_pennvest_grants_raw.json":    "PennVEST Water & Infrastructure",
    "pa_pema_grants_raw.json":        "PEMA Emergency Management Grants",
    "pa_agriculture_grants_raw.json": "PA Dept of Agriculture Grants",
    # DC
    "dc_central_grants_raw.json":  "DC Central Grants Hub",
    "dc_ovsjg_grants_raw.json":    "DC OVSJG Grants",
    "dc_dslbd_grants_raw.json":    "DC Small Business Grants",
    # Maryland
    "md_grants_portal_raw.json":   "Maryland Governor's Grants Portal",
    "md_commerce_grants_raw.json": "Maryland Commerce Funding",
    "md_bworks_grants_raw.json":   "Maryland Business Works",
    "md_dhcd_grants_raw.json":     "Maryland DHCD Housing",
    "md_grants_portal_raw.json":   "Maryland Governor's Grants Portal",
    "md_msde_grants_raw.json":     "MD State Dept of Education Grants",
    # New York
    "ny_esd_grants_raw.json":      "NY ESD Grants",
    "ny_grants_gateway_raw.json":  "NY Grants Gateway (SFS Browse Portal)",
    "ny_nyserda_grants_raw.json":  "NY NYSERDA",
    "ny_gov_grants_raw.json":      "NY Gov Grants",
    "ny_empire_grants_raw.json":   "NY Empire State Development",
    "ny_dos_grants_raw.json":      "NY Dept of State – Community Grants",
    "ny_nysca_grants_raw.json":    "NY State Council on the Arts",
    "ny_health_grants_raw.json":   "NY Dept of Health Grant Programs",
    "ny_ocfs_grants_raw.json":     "NY Office of Children & Family Services",
    "ny_nysed_grants_raw.json":    "NY State Education Dept Grants",
    "ny_homes_grants_raw.json":    "NY Homes & Community Renewal",
    # PA Grants Search
    "pa_grants_search_raw.json":   "PA Grants Search",
}
SOURCE_URLS = {
    # Pennsylvania
    "pa_dced_grants_raw.json":        "https://dced.pa.gov/programs/",
    "pa_gov_grants_raw.json":         "https://www.pa.gov/guides/grants/",
    "pa_dli_grants_raw.json":         "https://www.dli.pa.gov/Businesses/Workforce-Development/Pages/Grants.aspx",
    "pa_dcnr_grants_raw.json":        "https://www.dcnr.pa.gov/Grants/Pages/default.aspx",
    "pa_pennvest_grants_raw.json":    "https://www.pennvestinvestments.com/",
    "pa_pema_grants_raw.json":        "https://www.pema.pa.gov/Grants/Pages/default.aspx",
    "pa_agriculture_grants_raw.json": "https://www.agriculture.pa.gov/Grants/Pages/default.aspx",
    # DC
    "dc_central_grants_raw.json":  "https://dc.gov/page/grants-and-funding",
    "dc_ovsjg_grants_raw.json":    "https://ovsjg.dc.gov/page/funding-opportunities-current",
    "dc_dslbd_grants_raw.json":    "https://dslbd.dc.gov/",
    # Maryland
    "md_commerce_grants_raw.json": "https://commerce.maryland.gov/fund",
    "md_bworks_grants_raw.json":   "https://bworks.maryland.gov/",
    "md_dhcd_grants_raw.json":     "https://dhcd.maryland.gov/",
    "md_grants_portal_raw.json":   "https://grants.maryland.gov/Pages/StateGrants.aspx",
    "md_msde_grants_raw.json":     "https://marylandpublicschools.org/about/pages/ofpos/gac/grantprograms/index.aspx",
    # New York
    "ny_esd_grants_raw.json":      "https://esd.ny.gov/",
    "ny_grants_gateway_raw.json":  "https://grantsgateway.ny.gov/IntelliGrants_NYSGG/module/nysgg/goportal.aspx?NavItem1=2",
    "ny_nyserda_grants_raw.json":  "https://www.nyserda.ny.gov/",
    "ny_gov_grants_raw.json":      "https://www.grants.ny.gov/",
    "ny_empire_grants_raw.json":   "https://esd.ny.gov/funding-opportunities",
    "ny_dos_grants_raw.json":      "https://www.dos.ny.gov/funding/index.html",
    "ny_nysca_grants_raw.json":    "https://www.nysca.org/apply/",
    "ny_health_grants_raw.json":   "https://www.health.ny.gov/funding/",
    "ny_ocfs_grants_raw.json":     "https://ocfs.ny.gov/main/grants/",
    "ny_nysed_grants_raw.json":    "https://www.nysed.gov/grants",
    "ny_homes_grants_raw.json":    "https://hcr.ny.gov/funding-opportunities",
    # PA Grants Search
    "pa_grants_search_raw.json":   "https://www.pa.gov/en/grants/search/",
}


def make_key(state_code: str, opportunity_url: str, application_url: str = "") -> str:
    """
    Use opportunity_url (the unique source page) as the dedup anchor.
    Multiple grants often share a single generic application portal URL,
    so application_url alone is not a reliable unique key.
    Falls back to application_url if opportunity_url is absent.
    Raises ValueError if both URLs are empty — prevents silent dedup collisions.
    """
    anchor = (opportunity_url or application_url or "").strip().lower().rstrip("/")
    if not anchor:
        raise ValueError(
            "make_key requires at least one non-empty URL "
            f"(state={state_code!r}, opportunity_url={opportunity_url!r}, "
            f"application_url={application_url!r})"
        )
    raw = f"{state_code.lower()}|{anchor}"
    return hashlib.sha256(raw.encode()).hexdigest()[:64]


def parse_deadline(dl_str):
    if not dl_str:
        return None
    dl_str = dl_str.strip()
    # Ordered from most-specific to least: try exact strptime formats first,
    # then fall back to dateutil for long-form strings like "June 30, 2026".
    for fmt in (
        "%m/%d/%Y",   # 06/30/2026
        "%Y-%m-%d",   # 2026-06-30
        "%m-%d-%Y",   # 06-30-2026
        "%B %d, %Y",  # June 30, 2026
        "%b %d, %Y",  # Jun 30, 2026
        "%d %B %Y",   # 30 June 2026
        "%d %b %Y",   # 30 Jun 2026
    ):
        try:
            return datetime.strptime(dl_str, fmt).isoformat()
        except ValueError:
            pass
    # dateutil fallback — handles remaining regional/abbreviation variants.
    # Only applied when a 4-digit year is present; without one dateutil would
    # silently infer the current year from a bare "06/30"-style string.
    if re.search(r"\b\d{4}\b", dl_str):
        try:
            from dateutil import parser as _dateutil_parser
            dt = _dateutil_parser.parse(dl_str, fuzzy=False)
            return dt.isoformat()
        except Exception:
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
    # application_url may be null (AI found no direct link); opportunity_url is the fallback.
    # A grant is still live-ready as long as it has at least one linkable URL.
    if not row["title"] or (not row["application_url"] and not row["opportunity_url"]):
        return False
    score = row["data_quality_score"] or 0
    if score < min_score:
        return False
    # Rolling grants are always accepting applications — status="unverified" doesn't block them.
    # Non-rolling grants must have an explicit active/rolling/expiring_soon status.
    rolling = bool(row["rolling"])
    if not rolling and (row["status"] or "").lower() not in LIVE_STATUSES:
        return False
    if not row["deadline"] and not rolling:
        return False
    # high-quality grants go live as-is
    # lower-scored grants need a significant award amount to be worth showing
    award_min = row["award_min"] or 0
    if score < HIGH_SCORE and award_min < MIN_AWARD:
        return False
    return True


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
        now = datetime.utcnow().isoformat()
        # Use savepoint so a failed INSERT doesn't abort the whole transaction
        try:
            conn.execute(text("SAVEPOINT create_source"))
            conn.execute(text("""
                INSERT INTO sources (name, url, state_id, scraper_type, scrape_frequency_hours,
                                    is_active, created_at, updated_at)
                VALUES (:name, :url, :sid, 'scraper', 24, TRUE, :now, :now)
            """), {"name": name, "url": url, "sid": sid, "now": now})
            conn.execute(text("RELEASE SAVEPOINT create_source"))
        except Exception as exc:
            conn.execute(text("ROLLBACK TO SAVEPOINT create_source"))
            # Source might already exist (race condition or duplicate) — try to fetch it
            row2 = conn.execute(
                text("SELECT id FROM sources WHERE name = :n OR url = :u"), {"n": name, "u": url}
            ).fetchone()
            if row2:
                _source_cache[source_file] = row2[0]
                return _source_cache[source_file]
            raise RuntimeError(f"Failed to create or find source: {name}") from exc
        row2 = conn.execute(
            text("SELECT id FROM sources WHERE name = :n"), {"n": name}
        ).fetchone()
        if row2 is None:
            raise RuntimeError(f"Could not find source after insert: '{source_file}'")
        _source_cache[source_file] = row2[0]
    return _source_cache[source_file]


def upsert_opportunity(conn, row, dry_run: bool) -> tuple:
    """Returns (action, opportunity_id, queued)."""
    state_code = (row["state"] or "").upper()
    opp_url    = (row["opportunity_url"] or "").strip()
    app_url    = (row["application_url"] or "").strip()
    # If AI found no verified apply link, fall back to the info page URL.
    # The listing UI can still link users somewhere meaningful.
    if not app_url:
        app_url = opp_url
    key        = make_key(state_code, opp_url, app_url)

    state_id   = get_state_id(conn, state_code)
    source_id  = get_or_create_source(conn, row["source_file"] or "")
    deadline   = parse_deadline(row["deadline"])
    tags_json  = parse_json_field(row["tags"])
    aof_json   = parse_json_field(row["areas_of_focus"])
    status     = STATUS_MAP.get((row["status"] or "").lower(), "unverified")

    # If the deadline has already passed and this is not a rolling grant, expire it
    if deadline and not bool(row["rolling"]):
        try:
            if datetime.fromisoformat(deadline).date() < date.today():
                status = "expired"
        except (ValueError, TypeError):
            pass

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
        elig_org   = True,
        elig_ind   = bool(row.get("eligibility_individual")),
        elig_desc  = row["eligibility_notes"],
        award_min  = row["award_min"],
        award_max  = row["award_max"],
        total_f    = row["total_funding"],
        deadline   = deadline,
        rolling    = bool(row["rolling"]),
        opp_url    = (row["opportunity_url"] or "")[:1000] or None,
        app_url    = app_url[:1000] or None,
        c_name     = (row["contact_name"]  or "")[:255] or None,
        c_email    = (row["contact_email"] or "")[:255] or None,
        tags       = tags_json,
        aof        = aof_json,
        industry   = (row["industry"] or "")[:255] or None,
        status     = status,
        score      = row["data_quality_score"],
        needs_rev  = bool(row["needs_review"]),
        synced_at  = datetime.utcnow().isoformat(),
        logo_url   = row.get("logo_url") or _get_logo_url(opp_url or app_url),
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
                logo_url                = :logo_url,
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
                logo_url,
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
                :logo_url,
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
            text("SELECT id FROM review_queue WHERE opportunity_id=:oid AND review_status='pending'"),
            {"oid": opp_id}
        ).fetchone()
        if not existing_q:
            reasons = []
            if score < REVIEW_BELOW:
                reasons.append(f"low_quality (score={score:.2f})")
            if needs:
                reasons.append("scraper_flagged")
            conn.execute(text("""
                INSERT INTO review_queue (opportunity_id, reason, review_status, created_at)
                VALUES (:oid, :reason, 'pending', NOW())
            """), {"oid": opp_id, "reason": "; ".join(reasons)})
            queued = True

    return action, opp_id, queued


def main(dry_run=False, min_score=MIN_SCORE):
    _ensure_opportunities_columns()

    # Expire stale grants first so the table is clean before we sync new data
    if not dry_run:
        expired_count = expire_stale_grants()
        if expired_count:
            log.info(f"Expired {expired_count} stale opportunities (deadline passed)")
        else:
            log.info("No stale opportunities to expire")

    # Read all scraped grants
    read_db = database.SessionLocal()
    try:
        all_rows = read_db.execute(text("SELECT * FROM scraped_grants")).mappings().fetchall()
        all_rows = list(all_rows)
    finally:
        read_db.close()

    live    = [r for r in all_rows if is_live_ready(r, min_score)]
    blocked = len(all_rows) - len(live)

    log.info(f"Total scraped_grants:  {len(all_rows)}")
    log.info(f"Live-ready to sync:    {len(live)}")
    log.info(f"Blocked:               {blocked}")
    if dry_run:
        log.info("DRY-RUN — no DB writes")

    stats = {"inserted": 0, "updated": 0, "queued": 0, "errors": 0}

    for row in live:
        # Each upsert gets its own session — one failing row never kills the batch
        row_db = database.SessionLocal()
        try:
            action, opp_id, queued = upsert_opportunity(row_db.connection(), row, dry_run)
            key = action.replace("would_", "")
            stats[key] = stats.get(key, 0) + 1
            if queued:
                stats["queued"] += 1
            if not dry_run:
                row_db.commit()
            else:
                row_db.rollback()
        except Exception as exc:
            stats["errors"] += 1
            log.error(f"  Error on '{(row['title'] or '')[:50]}': {exc}")
            try:
                row_db.rollback()
            except Exception:
                pass
        finally:
            row_db.close()

    inserted  = stats.get("inserted", 0)
    updated   = stats.get("updated", 0)
    unchanged = stats.get("unchanged", 0)
    errors    = stats["errors"]

    print(f"\n{'='*60}")
    print(f"SYNC COMPLETE{'  [DRY-RUN]' if dry_run else ''}")
    print(f"{'='*60}")
    print(f"Total processed:      {inserted + updated + unchanged + errors}")
    print(f"  Inserted:           {inserted}")
    print(f"  Updated:            {updated}")
    print(f"  Unchanged:          {unchanged}")
    print(f"  Errors:             {errors}")
    print(f"  Queued for review:  {stats['queued']}")
    print(f"  Blocked grants:     {blocked}  (below score threshold or missing deadline)")

    if not dry_run:
        db2 = database.SessionLocal()
        try:
            total  = db2.execute(text("SELECT COUNT(*) FROM opportunities")).scalar()
            active = db2.execute(text("SELECT COUNT(*) FROM opportunities WHERE status='active'")).scalar()
            pending= db2.execute(text("SELECT COUNT(*) FROM review_queue WHERE review_status='pending'")).scalar()
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
    parser.add_argument("--dry-run",     action="store_true")
    parser.add_argument("--min-score",   type=float, default=MIN_SCORE)
    parser.add_argument("--expire-only", action="store_true",
                        help="Only expire stale grants — skip full sync")
    args = parser.parse_args()

    if args.expire_only:
        n = expire_stale_grants()
        print(f"Expired {n} stale grant(s).")
    else:
        main(dry_run=args.dry_run, min_score=args.min_score)