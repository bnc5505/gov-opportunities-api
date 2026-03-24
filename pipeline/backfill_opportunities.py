#!/usr/bin/env python3
"""
backfill_opportunities.py
─────────────────────────
Phase 2 enrichment: directly enrich opportunities still missing description/summary
after enrich_scraped_grants.py + sync_opportunities.py.

For each opportunity lacking description:
  1. Fetch opportunity_url (fallback: application_url) via HTTP
  2. Strip HTML (remove nav/footer/script/style)
  3. Call Azure OpenAI with the same extraction prompt
  4. UPDATE opportunities directly
  5. Mirror update to scraped_grants WHERE application_url matches

CLI flags:
    --dry-run          Preview without API calls or DB writes
    --limit N          Process at most N opportunities
    --state CODE       Restrict to one state (e.g. MD, PA, NY, DC)
    --min-score FLOAT  Only enrich if data_quality_score < this (default 0.80)

Run from project root:
    .venv/bin/python pipeline/backfill_opportunities.py --dry-run --limit 5
    .venv/bin/python pipeline/backfill_opportunities.py --state MD
    .venv/bin/python pipeline/backfill_opportunities.py
"""

import sys
import os
import json
import re
import time
import argparse
import logging
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_DIR      = PROJECT_ROOT / "app"

sys.path.insert(0, str(APP_DIR))
os.chdir(str(APP_DIR))   # SQLite resolves to app/gov_grants.db

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from openai import AzureOpenAI
import database
from sqlalchemy import text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

TARGET_SCORE    = 0.80
AI_TEXT_LIMIT   = 8_000
FETCH_TIMEOUT   = 20        # seconds per HTTP request
API_DELAY_SEC   = 2         # pause between Azure OpenAI calls
THIS_YEAR       = datetime.utcnow().year
NEXT_YEAR       = THIS_YEAR + 1

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

SCORE_WEIGHTS = {
    "title":             0.15,
    "description":       0.10,
    "deadline":          0.15,
    "award_max":         0.10,
    "eligibility_notes": 0.10,
    "contact_email":     0.08,
    "application_url":   0.10,
    "tags":              0.07,
    "areas_of_focus":    0.07,
    "summary":           0.08,
}

EXTRACTION_PROMPT = """
You are an expert at reading government and foundation grant documents.

Analyze the text below and extract every piece of information you can find.
Return ONLY valid JSON — no markdown, no explanation, just the JSON object.

{{
  "title":                    "Full official grant title",
  "summary":                  "1-2 sentence plain-English summary of what this grant funds",
  "description":              "4-6 sentence description: purpose, who benefits, funding amount, how to apply",
  "deadline":                 "MM/DD/YYYY or null",
  "rolling":                  true or false,
  "is_annual":                true or false,
  "award_min":                number or null,
  "award_max":                number or null,
  "total_funding":            number or null,
  "award_text":               "e.g. up to $50,000",
  "eligibility_notes":        "plain English: who can apply and any restrictions",
  "eligible_applicant_types": ["list of types, e.g. Small Business, Nonprofit"],
  "tags":                     ["3-6 short keyword tags"],
  "areas_of_focus":           ["pick from: Capital, Networks, Capacity Building, Technical Assistance, Mentorship, Training, Housing, Workforce, Environment, Health, Education, Justice, Technology"],
  "industry":                 "primary industry sector or null",
  "contact_name":             "name or null",
  "contact_email":            "email address or null",
  "contact_phone":            "phone or null",
  "application_url":          "direct application form URL or null",
  "key_requirements":         ["3-5 key eligibility requirements"]
}}

RULES:
- Dates: convert ALL formats to MM/DD/YYYY. If only month+day with no year, use {THIS_YEAR} if not yet passed, else {NEXT_YEAR}.
- Amounts: convert $1M → 1000000, $50k → 50000. Never guess amounts not stated.
- If a field truly cannot be found in the text, return null — never fabricate.
- State context: {STATE}

TEXT TO ANALYZE (source: {SOURCE_URL}):
{PAGE_TEXT}
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def build_client():
    api_key  = os.getenv("AZURE_OPENAI_API_KEY")
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    deploy   = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")

    if not api_key:
        raise EnvironmentError(
            "\n[ERROR] AZURE_OPENAI_API_KEY is not set.\n"
            "Add it to your .env file:\n"
            "  AZURE_OPENAI_API_KEY=<your-key>\n"
        )
    if not endpoint:
        raise EnvironmentError("AZURE_OPENAI_ENDPOINT is not set in .env")

    return AzureOpenAI(
        api_key=api_key,
        api_version="2024-02-01",
        azure_endpoint=endpoint,
    ), deploy


def recalculate_score(fields: dict) -> float:
    score = 0.0
    for field, weight in SCORE_WEIGHTS.items():
        v = fields.get(field)
        if isinstance(v, list) and len(v) > 0:
            score += weight
        elif v:
            score += weight
    return round(score, 2)


def fetch_and_clean(url: str) -> str:
    """Fetch a URL and return clean body text, or '' on failure."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=FETCH_TIMEOUT)
        if resp.status_code != 200:
            log.warning(f"  HTTP {resp.status_code} for {url[:80]}")
            return ""
    except Exception as exc:
        log.warning(f"  Fetch error for {url[:80]}: {exc}")
        return ""

    soup = BeautifulSoup(resp.content, "html.parser")

    # Remove non-content elements
    for tag in soup(["script", "style", "nav", "footer", "header", "aside",
                     "noscript", "form", "iframe", "svg", "button"]):
        tag.decompose()

    text = soup.get_text(separator="\n", strip=True)
    # Collapse blank lines
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    clean = "\n".join(lines)

    if len(clean) < 200:
        log.warning(f"  Page too short ({len(clean)} chars) — likely error page")
        return ""

    return clean


def ai_extract(client, deploy, page_text: str, source_url: str,
               state: str, dry_run: bool = False):
    prompt = EXTRACTION_PROMPT.format(
        THIS_YEAR   = THIS_YEAR,
        NEXT_YEAR   = NEXT_YEAR,
        STATE       = state,
        SOURCE_URL  = source_url,
        PAGE_TEXT   = page_text[:AI_TEXT_LIMIT],
    )

    if dry_run:
        log.info(f"  [DRY-RUN] Would call AI with {min(len(page_text), AI_TEXT_LIMIT)} chars")
        return None

    try:
        resp = client.chat.completions.create(
            model=deploy,
            messages=[
                {"role": "system",  "content": "Extract structured grant data. Return valid JSON only."},
                {"role": "user",    "content": prompt},
            ],
            temperature=0,
            max_tokens=2000,
        )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r"^```json\s*", "", raw)
        raw = re.sub(r"```$",        "", raw).strip()
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning(f"  JSON parse error: {exc}")
        return None
    except Exception as exc:
        log.error(f"  Azure AI error: {exc}")
        return None


def parse_json_list(v):
    """Ensure a field is stored as a JSON string list."""
    if isinstance(v, list):
        return json.dumps(v)
    if isinstance(v, str) and v.startswith("["):
        return v
    return None


# ── State code lookup ─────────────────────────────────────────────────────────

_state_code_cache = {}

def get_state_code(conn, state_id: int) -> str:
    if state_id not in _state_code_cache:
        row = conn.execute(
            text("SELECT code FROM states WHERE id = :id"), {"id": state_id}
        ).fetchone()
        _state_code_cache[state_id] = row[0] if row else "XX"
    return _state_code_cache[state_id]


# ── Main ──────────────────────────────────────────────────────────────────────

def main(dry_run=False, limit=None, state_filter=None, min_score=TARGET_SCORE):
    if not dry_run:
        client, deploy = build_client()
    else:
        client, deploy = None, None
        log.info("DRY-RUN mode — no DB writes, no API calls")

    # Build the query
    where_clauses = [
        "(description IS NULL OR TRIM(description) = '')"
    ]
    params = {}

    if state_filter:
        # Join to states table to filter by code
        where_clauses.append(
            "state_id IN (SELECT id FROM states WHERE code = :state_code)"
        )
        params["state_code"] = state_filter.upper()

    where_sql = " AND ".join(where_clauses)
    query_sql = f"""
        SELECT o.id, o.title, o.state_id,
               o.opportunity_url, o.application_url,
               o.data_quality_score, o.description, o.summary,
               o.deadline, o.rolling, o.award_min, o.award_max,
               o.tags, o.opportunity_gap_resources,
               o.contact_email, o.contact_name, o.industry,
               o.eligibility_description
        FROM opportunities o
        WHERE {where_sql}
        ORDER BY o.data_quality_score ASC NULLS FIRST
    """

    db = database.SessionLocal()
    try:
        rows = db.execute(text(query_sql), params).mappings().fetchall()
    finally:
        db.close()

    rows = list(rows)
    if limit:
        rows = rows[:limit]

    total       = len(rows)
    enriched    = 0
    skipped_404 = 0
    skipped_ai  = 0
    scores_before = [r["data_quality_score"] or 0 for r in rows]

    log.info(f"Opportunities to backfill: {total}"
             + (f"  (state={state_filter})" if state_filter else ""))

    for i, row in enumerate(rows, 1):
        opp_id = row["id"]
        title  = row["title"] or ""
        url    = row["opportunity_url"] or row["application_url"] or ""
        app_url = row["application_url"] or ""

        # Lookup state code for logging
        db = database.SessionLocal()
        state_code = get_state_code(db, row["state_id"] or 0)
        db.close()

        log.info(f"[{i}/{total}] {state_code} | {title[:60]}")

        if not url:
            log.warning("  No URL — skipping")
            skipped_404 += 1
            continue

        page_text = fetch_and_clean(url)
        if not page_text:
            skipped_404 += 1
            continue

        ai_result = ai_extract(client, deploy, page_text, url, state_code, dry_run)

        if dry_run:
            enriched += 1
            continue

        if not ai_result:
            skipped_ai += 1
            continue

        # Compute new score using both existing and new fields
        score_fields = {
            "title":             row["title"],
            "description":       ai_result.get("description") or row["description"],
            "deadline":          ai_result.get("deadline") or row["deadline"],
            "award_max":         ai_result.get("award_max") or row["award_max"],
            "eligibility_notes": ai_result.get("eligibility_notes") or row["eligibility_description"],
            "contact_email":     ai_result.get("contact_email") or row["contact_email"],
            "application_url":   ai_result.get("application_url") or app_url,
            "tags":              ai_result.get("tags") or [],
            "areas_of_focus":    ai_result.get("areas_of_focus") or [],
            "summary":           ai_result.get("summary") or row["summary"],
        }
        new_score = recalculate_score(score_fields)

        # Parse deadline
        deadline_iso = None
        dl_str = ai_result.get("deadline")
        if dl_str:
            for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
                try:
                    deadline_iso = datetime.strptime(dl_str.strip(), fmt).isoformat()
                    break
                except ValueError:
                    pass

        tags_json = parse_json_list(ai_result.get("tags"))
        aof_json  = parse_json_list(ai_result.get("areas_of_focus"))

        # Resolve rolling: prefer AI result if existing is None
        rolling_val = ai_result.get("rolling")
        if rolling_val is None:
            rolling_val = row["rolling"]

        # Only set deadline if AI found one OR existing is None
        deadline_final = deadline_iso if deadline_iso else None

        db = database.SessionLocal()
        try:
            db.execute(text("""
                UPDATE opportunities SET
                    description              = :description,
                    summary                  = :summary,
                    eligibility_description  = :eligibility,
                    tags                     = :tags,
                    opportunity_gap_resources = :aof,
                    industry                 = :industry,
                    contact_email            = :contact_email,
                    contact_name             = :contact_name,
                    data_quality_score       = :score,
                    needs_review             = :needs_review,
                    updated_at               = :now
                WHERE id = :id
            """), {
                "id":           opp_id,
                "description":  ai_result.get("description"),
                "summary":      ai_result.get("summary"),
                "eligibility":  ai_result.get("eligibility_notes"),
                "tags":         tags_json,
                "aof":          aof_json,
                "industry":     ai_result.get("industry"),
                "contact_email":ai_result.get("contact_email"),
                "contact_name": ai_result.get("contact_name"),
                "score":        new_score,
                "needs_review": new_score < 0.5,
                "now":          datetime.utcnow().isoformat(),
            })

            # Also optionally set deadline/rolling if they're missing
            if deadline_final and not row["deadline"]:
                db.execute(text(
                    "UPDATE opportunities SET deadline = :dl WHERE id = :id"
                ), {"dl": deadline_final, "id": opp_id})

            if rolling_val is not None and row["rolling"] is None:
                db.execute(text(
                    "UPDATE opportunities SET rolling = :r WHERE id = :id"
                ), {"r": rolling_val, "id": opp_id})

            # Mirror to scraped_grants for consistency
            if app_url:
                db.execute(text("""
                    UPDATE scraped_grants SET
                        description        = :description,
                        summary            = :summary,
                        eligibility_notes  = :eligibility,
                        tags               = :tags,
                        areas_of_focus     = :aof,
                        industry           = :industry,
                        data_quality_score = :score
                    WHERE application_url = :app_url
                """), {
                    "description":  ai_result.get("description"),
                    "summary":      ai_result.get("summary"),
                    "eligibility":  ai_result.get("eligibility_notes"),
                    "tags":         tags_json,
                    "aof":          aof_json,
                    "industry":     ai_result.get("industry"),
                    "score":        new_score,
                    "app_url":      app_url,
                })

            db.commit()
            enriched += 1
            old_score = row["data_quality_score"] or 0
            log.info(f"  Score: {old_score:.2f} → {new_score:.2f}  | {title[:50]}")

        except Exception as exc:
            db.rollback()
            log.error(f"  DB write error: {exc}")
            skipped_ai += 1
        finally:
            db.close()

        time.sleep(API_DELAY_SEC)

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"BACKFILL COMPLETE")
    print(f"{'='*60}")
    print(f"Processed:              {total}")
    print(f"Enriched:               {enriched}")
    print(f"Skipped (fetch failed): {skipped_404}")
    print(f"Skipped (AI failed):    {skipped_ai}")

    if not dry_run and enriched > 0:
        db = database.SessionLocal()
        try:
            c = db.execute(
                text("SELECT COUNT(*) FROM opportunities WHERE description IS NULL OR TRIM(description) = ''")
            ).fetchone()
            avg = db.execute(
                text("SELECT AVG(data_quality_score) FROM opportunities")
            ).fetchone()
            above = db.execute(
                text("SELECT COUNT(*) FROM opportunities WHERE data_quality_score >= 0.7")
            ).fetchone()
            print(f"\nOpportunities still missing description: {c[0]}")
            print(f"Avg quality score (all opps):            {avg[0]:.3f}")
            print(f"Opportunities with score >= 0.7:         {above[0]}")
        finally:
            db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill missing descriptions in opportunities via live URL fetch + Azure AI"
    )
    parser.add_argument("--dry-run",   action="store_true",
                        help="Preview without API calls or DB writes")
    parser.add_argument("--limit",     type=int, default=None,
                        help="Process at most N opportunities")
    parser.add_argument("--state",     type=str, default=None,
                        help="Filter to one state code (e.g. MD, PA)")
    parser.add_argument("--min-score", type=float, default=TARGET_SCORE,
                        help=f"Enrich if data_quality_score < this (default {TARGET_SCORE})")
    args = parser.parse_args()

    main(
        dry_run=args.dry_run,
        limit=args.limit,
        state_filter=args.state,
        min_score=args.min_score,
    )
