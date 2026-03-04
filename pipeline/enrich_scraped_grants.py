import sys
import os
import json
import re
import time
import argparse
import logging
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent   # pipeline/../ = project root
APP_DIR      = PROJECT_ROOT / "app"
DATA_ROOT    = PROJECT_ROOT / "data"

sys.path.insert(0, str(APP_DIR))
os.chdir(str(APP_DIR))           # SQLite resolves to app/gov_grants.db

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

TARGET_SCORE    = 0.80   # enrich everything below this
AI_TEXT_LIMIT   = 8_000  # chars of combined_text sent to the model
RETRY_DELAY_SEC = 2      # pause between API calls to avoid throttling
THIS_YEAR       = datetime.utcnow().year
NEXT_YEAR       = THIS_YEAR + 1

# auto-discovered in build_text_index()
_STATE_DIRS = ["dc", "md", "ny", "pa"]

# mirrors base_scraper.calculate_quality_score
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

def recalculate_score(fields: dict) -> float:
    score = 0.0
    for field, weight in SCORE_WEIGHTS.items():
        v = fields.get(field)
        if isinstance(v, list) and len(v) > 0:
            score += weight
        elif v:
            score += weight
    return round(score, 2)


def build_text_index():
    """
    Returns a dict keyed by application_url → combined_text
    and also by lower(title) → combined_text as fallback.
    Auto-discovers all *_raw.json files across data/dc/, data/md/, data/ny/, data/pa/.
    """
    url_index   = {}
    title_index = {}
    for state in _STATE_DIRS:
        state_dir = DATA_ROOT / state
        if not state_dir.exists():
            continue
        for path in sorted(state_dir.glob("*_raw.json")):
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
                for g in data.get("grants", []):
                    ct = g.get("combined_text") or ""
                    if not ct:
                        continue
                    url = (g.get("application_url") or "").strip().lower()
                    if url:
                        url_index[url] = (ct, g.get("source_url", ""), g.get("state", ""))
                    title = (g.get("title") or "").strip().lower()
                    if title:
                        title_index[title] = (ct, g.get("source_url", ""), g.get("state", ""))
            except Exception as exc:
                log.warning(f"Could not read {path.name}: {exc}")
    log.info(f"Text index built: {len(url_index)} by URL, {len(title_index)} by title")
    return url_index, title_index


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
{COMBINED_TEXT}
"""

def ai_extract(client, deploy, combined_text, source_url, state, dry_run=False):
    prompt = EXTRACTION_PROMPT.format(
        THIS_YEAR    = THIS_YEAR,
        NEXT_YEAR    = NEXT_YEAR,
        STATE        = state,
        SOURCE_URL   = source_url,
        COMBINED_TEXT= combined_text[:AI_TEXT_LIMIT],
    )

    if dry_run:
        log.info(f"  [DRY-RUN] Would call Azure OpenAI with {len(combined_text[:AI_TEXT_LIMIT])} chars")
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
        log.warning(f"  JSON parse error: {exc} — raw: {raw[:200]}")
        return None
    except Exception as exc:
        log.error(f"  Azure AI error: {exc}")
        return None


def merge(row: dict, ai: dict) -> dict:
    """
    Apply AI-extracted values to an existing row dict.
    Only overwrite a field if the row's current value is null/empty
    AND the AI returned something non-null.
    Exception: always overwrite description/summary/tags/areas_of_focus
    since those were empty in all rows.
    """
    ALWAYS_OVERWRITE = {
        "description", "summary", "eligibility_notes",
        "tags", "areas_of_focus", "contact_email",
        "contact_name", "contact_phone", "key_requirements",
        "industry", "award_text",
    }
    ONLY_IF_EMPTY = {
        "title", "deadline", "rolling", "is_annual",
        "award_min", "award_max", "total_funding",
        "application_url",
    }

    updated = dict(row)

    for field in ALWAYS_OVERWRITE:
        val = ai.get(field)
        if val is not None and val != "" and val != []:
            updated[field] = val

    for field in ONLY_IF_EMPTY:
        val = ai.get(field)
        if val is not None and val != "" and not row.get(field):
            updated[field] = val

    return updated


def main(dry_run=False, limit=None, min_score=TARGET_SCORE):
    if not dry_run:
        client, deploy = build_client()
    else:
        client, deploy = None, None
        log.info("DRY-RUN mode — no DB writes, no API calls")

    url_index, title_index = build_text_index()

    db = database.SessionLocal()
    try:
        rows = db.execute(
            text("SELECT * FROM scraped_grants WHERE data_quality_score < :s ORDER BY data_quality_score DESC"),
            {"s": min_score}
        ).mappings().fetchall()
    finally:
        db.close()

    rows = list(rows)
    if limit:
        rows = rows[:limit]

    total      = len(rows)
    enriched   = 0
    skipped_no_text = 0
    skipped_ai_fail = 0
    score_before = [r["data_quality_score"] or 0 for r in rows]

    log.info(f"Grants to enrich: {total}  (score < {min_score})")

    for i, row in enumerate(rows, 1):
        title = row["title"] or ""
        state = row["state"] or ""
        app_url = (row["application_url"] or "").lower()

        log.info(f"[{i}/{total}] {state} | {title[:60]}")

        entry = url_index.get(app_url) or title_index.get(title.lower())
        if not entry:
            log.warning(f"  No combined_text found — skipping")
            skipped_no_text += 1
            continue

        combined_text, source_url, _ = entry

        ai_result = ai_extract(client, deploy, combined_text, source_url, state, dry_run=dry_run)

        if dry_run:
            enriched += 1
            continue

        if not ai_result:
            skipped_ai_fail += 1
            continue

        row_dict = dict(row)
        merged   = merge(row_dict, ai_result)

        # Serialize list fields to JSON strings for storage
        for list_field in ("tags", "areas_of_focus", "eligible_applicant_types", "key_requirements"):
            v = merged.get(list_field)
            if isinstance(v, list):
                merged[list_field] = json.dumps(v)

        new_score = recalculate_score({
            **merged,
            "tags":          json.loads(merged.get("tags") or "[]"),
            "areas_of_focus":json.loads(merged.get("areas_of_focus") or "[]"),
        })
        merged["data_quality_score"] = new_score
        merged["needs_review"]       = new_score < 0.5

        db = database.SessionLocal()
        try:
            db.execute(text("""
                UPDATE scraped_grants SET
                    description        = :description,
                    summary            = :summary,
                    eligibility_notes  = :eligibility_notes,
                    contact_email      = :contact_email,
                    contact_name       = :contact_name,
                    tags               = :tags,
                    areas_of_focus     = :areas_of_focus,
                    industry           = :industry,
                    award_text         = :award_text,
                    award_min          = :award_min,
                    award_max          = :award_max,
                    deadline           = :deadline,
                    rolling            = :rolling,
                    is_annual          = :is_annual,
                    data_quality_score = :data_quality_score,
                    needs_review       = :needs_review
                WHERE id = :id
            """), {
                "id":                 merged["id"],
                "description":        merged.get("description"),
                "summary":            merged.get("summary"),
                "eligibility_notes":  merged.get("eligibility_notes"),
                "contact_email":      merged.get("contact_email"),
                "contact_name":       merged.get("contact_name"),
                "tags":               merged.get("tags"),
                "areas_of_focus":     merged.get("areas_of_focus"),
                "industry":           merged.get("industry"),
                "award_text":         merged.get("award_text"),
                "award_min":          merged.get("award_min"),
                "award_max":          merged.get("award_max"),
                "deadline":           merged.get("deadline"),
                "rolling":            merged.get("rolling"),
                "is_annual":          merged.get("is_annual"),
                "data_quality_score": new_score,
                "needs_review":       merged.get("needs_review"),
            })
            db.commit()
            enriched += 1
            log.info(f"  Score: {row['data_quality_score']:.2f} → {new_score:.2f}")
        except Exception as exc:
            db.rollback()
            log.error(f"  DB write error: {exc}")
            skipped_ai_fail += 1
        finally:
            db.close()

        # Brief pause to avoid rate-limiting
        time.sleep(RETRY_DELAY_SEC)

    print(f"\n{'='*60}")
    print(f"ENRICHMENT COMPLETE")
    print(f"{'='*60}")
    print(f"Processed:        {total}")
    print(f"Enriched:         {enriched}")
    print(f"Skipped (no text):{skipped_no_text}")
    print(f"Skipped (AI fail):{skipped_ai_fail}")

    if not dry_run and enriched > 0:
        db = database.SessionLocal()
        try:
            rows_after = db.execute(
                text("SELECT data_quality_score, title, state FROM scraped_grants ORDER BY data_quality_score DESC")
            ).fetchall()
            scores = [r[0] or 0 for r in rows_after]
            above_target = sum(1 for s in scores if s >= min_score)
            avg = sum(scores) / len(scores) if scores else 0
            print(f"\nAverage score after:  {avg:.2f}  (was {sum(score_before)/len(score_before):.2f})")
            print(f"Grants >= {min_score}:       {above_target} / {len(scores)}")
            print(f"\nTop 10 by score after enrichment:")
            print(f"  {'Score':>6}  {'State':>5}  Title")
            print(f"  {'-'*6}  {'-'*5}  {'-'*50}")
            for score, title, state in rows_after[:10]:
                print(f"  {score or 0:>6.2f}  {state or '?':>5}  {(title or '')[:55]}")
        finally:
            db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Enrich scraped_grants using Azure OpenAI")
    parser.add_argument("--dry-run",   action="store_true", help="Preview without API calls or DB writes")
    parser.add_argument("--limit",     type=int,   default=None, help="Only process first N grants")
    parser.add_argument("--min-score", type=float, default=TARGET_SCORE, help=f"Enrich grants below this score (default {TARGET_SCORE})")
    args = parser.parse_args()

    main(dry_run=args.dry_run, limit=args.limit, min_score=args.min_score)
