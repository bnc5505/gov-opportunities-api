import sys
import os
import json
import re
import time
import argparse
import logging
import threading
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent   # pipeline/../ = project root
APP_DIR      = PROJECT_ROOT / "app"
DATA_ROOT    = PROJECT_ROOT / "data"

sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(str(APP_DIR))           # SQLite resolves to app/gov_grants.db

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from openai import AzureOpenAI
import database
from sqlalchemy import text
from pipeline.constants import (
    TARGET_SCORE,
    AI_TEXT_LIMIT,
    AI_CALL_TIMEOUT,
    PASS2_LIMIT_DEFAULT,
    MAX_AI_CONCURRENT,
    MAX_HTTP_CONCURRENT,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

THIS_YEAR = datetime.utcnow().year
NEXT_YEAR = THIS_YEAR + 1

_AI_SEM   = threading.Semaphore(MAX_AI_CONCURRENT)
_HTTP_SEM = threading.Semaphore(MAX_HTTP_CONCURRENT)

_STATE_DIRS = ["dc", "md", "ny", "pa"]

REQUEST_TIMEOUT = 20   # seconds for Pass 2 page fetches
REQUEST_DELAY   = 0.5  # polite delay between Pass 2 fetches
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# Apply and PDF link patterns used in Pass 2 link extraction
APPLY_LINK_PATTERNS = re.compile(
    r"apply|application|how.to.apply|submit.proposal|submit.application|"
    r"grant.application|apply.now|apply.here|apply.online",
    re.IGNORECASE,
)
PDF_LINK_PATTERNS = re.compile(
    r"guidelines|application|rfp|nofa|solicitation",
    re.IGNORECASE,
)

# ── Quality score weights (mirrors base_scraper.calculate_quality_score) ──
SCORE_WEIGHTS = {
    "title":             0.15,
    "description":       0.15,
    "award_max":         0.12,
    "application_url":   0.12,
    "eligibility_notes": 0.10,
    "summary":           0.08,
    "tags":              0.05,
    "areas_of_focus":    0.05,
    "contact_email":     0.03,
}
# deadline and rolling share a single 0.15 slot — handled separately


def recalculate_score(fields: dict) -> float:
    score = 0.0
    for field, weight in SCORE_WEIGHTS.items():
        v = fields.get(field)
        if isinstance(v, list) and len(v) > 0:
            score += weight
        elif v:
            score += weight
    if fields.get("deadline") or fields.get("rolling"):
        score += 0.15
    return round(score, 2)



def build_text_index():
    """
    Returns url_index and title_index dicts mapping → (combined_text, source_url, state).
    Used by Pass 1 to find pre-scraped text without re-fetching pages.
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


# AI_CALL_TIMEOUT is imported from pipeline.constants


def _call_ai(client, deploy, system_msg: str, user_msg: str, max_tokens: int = 2000):
    """Shared helper for all AI calls. Returns parsed dict or None."""
    from openai import APITimeoutError
    try:
        with _AI_SEM:
            resp = client.chat.completions.create(
                model=deploy,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user",   "content": user_msg},
                ],
                temperature=0,
                max_tokens=max_tokens,
                timeout=AI_CALL_TIMEOUT,
            )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r"^```json\s*", "", raw)
        raw = re.sub(r"```$",        "", raw).strip()
        return json.loads(raw)
    except APITimeoutError:
        log.warning("  Azure AI timeout — skipping row (will retry next run)")
        return None
    except json.JSONDecodeError as exc:
        log.warning(f"  JSON parse error: {exc} — raw snippet: {raw[:200]}")
        return None
    except Exception as exc:
        log.error(f"  Azure AI error: {exc}")
        return None


# Pass 1: extract fields from cached combined_text

PASS1_PROMPT = """
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


def _pass1_enrich_one(row, client, deploy, url_index, title_index, dry_run):
    """
    Pass 1: extract from cached combined_text.
    Returns (row_id, merged_dict_or_None, status_str, old_score).
    """
    row_dict  = dict(row)
    title     = row_dict.get("title") or ""
    state     = row_dict.get("state") or ""
    app_url   = (row_dict.get("application_url") or "").lower()
    old_score = row_dict.get("data_quality_score") or 0

    entry = url_index.get(app_url) or title_index.get(title.lower())
    if not entry:
        return row_dict["id"], None, "no_text", old_score

    combined_text, source_url, _ = entry

    if dry_run:
        log.info(f"  [DRY-RUN P1] Would call AI with {min(len(combined_text), AI_TEXT_LIMIT)} chars")
        return row_dict["id"], None, "dry_run", old_score

    prompt = PASS1_PROMPT.format(
        THIS_YEAR     = THIS_YEAR,
        NEXT_YEAR     = NEXT_YEAR,
        STATE         = state,
        SOURCE_URL    = source_url,
        COMBINED_TEXT = combined_text[:AI_TEXT_LIMIT],
    )
    ai_result = _call_ai(
        client, deploy,
        system_msg="Extract structured grant data. Return valid JSON only.",
        user_msg=prompt,
    )
    if not ai_result:
        return row_dict["id"], None, "ai_fail", old_score

    merged = _merge_pass1(row_dict, ai_result)
    merged = _finalise(merged)
    return row_dict["id"], merged, "ok", old_score


def _merge_pass1(row: dict, ai: dict) -> dict:
    """
    Apply Pass 1 AI values. Always overwrites qualitative fields;
    only fills empty slots for structured fields.
    """
    ALWAYS_OVERWRITE = {
        "description", "summary", "eligibility_notes",
        "tags", "areas_of_focus", "contact_email",
        "contact_name", "contact_phone", "key_requirements",
        "industry", "award_text",
        "application_url",   # AI's verified apply link always wins
    }
    ONLY_IF_EMPTY = {
        "title", "deadline", "rolling", "is_annual",
        "award_min", "award_max", "total_funding",
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


# Pass 2: deep link extraction for rows still missing apply URL or deadline

PASS2_PROMPT = """
I found these links on a government grant page. Which one is the \
direct application or submission link? Also extract the deadline \
and award amounts if visible.

Links found:
{LINKS}

Surrounding text:
{CONTEXT}

Return JSON only — no markdown, no explanation:
{{
  "application_url": "the direct apply/submit URL or null",
  "deadline":        "MM/DD/YYYY or null",
  "award_max":       number or null,
  "award_min":       number or null
}}

RULES:
- application_url must be a URL that leads directly to an application form or submission portal.
  Do NOT return a general grants page, a news article, or a contact page.
- Dates: convert to MM/DD/YYYY. Use {THIS_YEAR} if the date has not yet passed, else {NEXT_YEAR}.
- Amounts: convert $1M → 1000000, $50k → 50000.
- If a field cannot be determined with confidence, return null.
- If multiple apply links exist, prefer the one with the shortest \
path that contains "apply", "submit", or "application" in the URL \
itself. Ignore links to PDFs unless no HTML application form exists.
"""


def _fetch_page(url: str):
    """Fetch a page for Pass 2. Returns (html_text, final_url) or (None, None)."""
    try:
        time.sleep(REQUEST_DELAY)
        with _HTTP_SEM:
            r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if r.status_code == 200:
            return r.text, r.url
        log.warning(f"  P2 HTTP {r.status_code} for {url}")
    except Exception as exc:
        log.warning(f"  P2 fetch error for {url}: {exc}")
    return None, None


def _extract_apply_links(html: str, base_url: str) -> list[dict]:
    """
    Parse the page HTML and return candidate apply links.
    Each entry: {"text": ..., "href": ..., "context": ...}
    """
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    candidates = []
    seen_hrefs = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#") or href.startswith("mailto:"):
            continue

        full_href = urljoin(base_url, href)
        if full_href in seen_hrefs:
            continue

        link_text = a.get_text(separator=" ", strip=True)

        # Match on link text OR href for apply patterns
        text_match = APPLY_LINK_PATTERNS.search(link_text)
        href_match = APPLY_LINK_PATTERNS.search(href)
        # Match PDF links with grant-related names
        pdf_match  = href.lower().endswith(".pdf") and PDF_LINK_PATTERNS.search(href)

        if not (text_match or href_match or pdf_match):
            continue

        # Grab up to 200 chars of surrounding page text for context
        parent = a.parent
        context = (parent.get_text(separator=" ", strip=True) if parent else "")[:200]

        candidates.append({
            "text":    link_text[:150],
            "href":    full_href,
            "context": context,
        })
        seen_hrefs.add(full_href)

        if len(candidates) >= 20:  # cap to keep prompt size reasonable
            break

    return candidates


def _pass2_enrich_one(row, client, deploy, dry_run):
    """
    Pass 2: fetch opportunity_url fresh, scan for apply links, ask GPT-4o.
    Returns (row_id, updates_dict_or_None, improved: bool).
    """
    row_dict = dict(row)
    row_id   = row_dict["id"]
    opp_url  = (row_dict.get("opportunity_url") or "").strip()
    state    = row_dict.get("state") or ""

    if not opp_url:
        return row_id, None, False

    if dry_run:
        log.info(f"  [DRY-RUN P2] Would fetch {opp_url}")
        return row_id, None, False

    html, final_url = _fetch_page(opp_url)
    if not html:
        return row_id, None, False

    candidates = _extract_apply_links(html, final_url or opp_url)
    if not candidates:
        log.info(f"  P2 no candidate links found on {opp_url}")
        return row_id, None, False

    links_text   = "\n".join(f'- "{c["text"]}" → {c["href"]}' for c in candidates)
    context_text = "\n".join(f'[{c["href"]}] {c["context"]}' for c in candidates)

    prompt = PASS2_PROMPT.format(
        LINKS     = links_text,
        CONTEXT   = context_text[:3000],
        THIS_YEAR = THIS_YEAR,
        NEXT_YEAR = NEXT_YEAR,
    )

    ai_result = _call_ai(
        client, deploy,
        system_msg="You identify direct grant application links. Return valid JSON only.",
        user_msg=prompt,
        max_tokens=400,
    )
    if not ai_result:
        return row_id, None, False

    updates = {}
    app_url = (ai_result.get("application_url") or "").strip()
    if app_url:
        updates["application_url"]          = app_url[:1000]
        updates["application_url_verified"] = True

    # Only fill deadline/award if currently missing
    if not row_dict.get("deadline") and not row_dict.get("rolling"):
        deadline = ai_result.get("deadline")
        if deadline:
            updates["deadline"] = deadline

    if not row_dict.get("award_max") and ai_result.get("award_max"):
        updates["award_max"] = ai_result["award_max"]
    if not row_dict.get("award_min") and ai_result.get("award_min"):
        updates["award_min"] = ai_result["award_min"]

    improved = bool(updates)
    return row_id, updates if improved else None, improved


def _write_pass2_result(row_id, updates, row_dict):
    """
    Write Pass 2 updates back to DB. Recalculates score with merged fields.
    """
    db = database.SessionLocal()
    try:
        # Merge updates into row for score recalculation
        merged_for_score = {**row_dict, **updates}
        new_score = recalculate_score({
            **merged_for_score,
            "tags":           json.loads(merged_for_score.get("tags") or "[]"),
            "areas_of_focus": json.loads(merged_for_score.get("areas_of_focus") or "[]"),
        })
        updates["data_quality_score"] = new_score
        updates["needs_review"]       = new_score < 0.5

        set_clauses = ", ".join(f"{k} = :{k}" for k in updates)
        db.execute(
            text(f"UPDATE scraped_grants SET {set_clauses} WHERE id = :row_id"),
            {**updates, "row_id": row_id},
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        log.error(f"  P2 DB write error for id={row_id}: {exc}")
        raise
    finally:
        db.close()


# Pass 1 write helpers

def _finalise(merged: dict) -> dict:
    """Serialize list fields to JSON strings and compute score."""
    for list_field in ("tags", "areas_of_focus", "eligible_applicant_types", "key_requirements"):
        v = merged.get(list_field)
        if isinstance(v, list):
            merged[list_field] = json.dumps(v)

    new_score = recalculate_score({
        **merged,
        "tags":           json.loads(merged.get("tags") or "[]"),
        "areas_of_focus": json.loads(merged.get("areas_of_focus") or "[]"),
    })
    merged["data_quality_score"] = new_score
    merged["needs_review"]       = new_score < 0.5
    return merged


def _write_pass1_result(row_id, merged):
    """Write Pass 1 result to DB. Thread-safe — opens its own session."""
    db = database.SessionLocal()
    try:
        app_url_verified = bool(merged.get("application_url"))
        db.execute(text("""
            UPDATE scraped_grants SET
                description              = :description,
                summary                  = :summary,
                eligibility_notes        = :eligibility_notes,
                contact_email            = :contact_email,
                contact_name             = :contact_name,
                contact_phone            = :contact_phone,
                tags                     = :tags,
                areas_of_focus           = :areas_of_focus,
                industry                 = :industry,
                award_text               = :award_text,
                award_min                = :award_min,
                award_max                = :award_max,
                deadline                 = :deadline,
                rolling                  = :rolling,
                is_annual                = :is_annual,
                application_url          = :application_url,
                application_url_verified = :application_url_verified,
                key_requirements         = :key_requirements,
                data_quality_score       = :data_quality_score,
                needs_review             = :needs_review,
                enriched_at              = :enriched_at
            WHERE id = :id
        """), {
            "id":                       row_id,
            "description":              merged.get("description"),
            "summary":                  merged.get("summary"),
            "eligibility_notes":        merged.get("eligibility_notes"),
            "contact_email":            merged.get("contact_email"),
            "contact_name":             merged.get("contact_name"),
            "contact_phone":            (merged.get("contact_phone") or "")[:100] or None,
            "tags":                     merged.get("tags"),
            "areas_of_focus":           merged.get("areas_of_focus"),
            "industry":                 merged.get("industry"),
            "award_text":               merged.get("award_text"),
            "award_min":                merged.get("award_min"),
            "award_max":                merged.get("award_max"),
            "deadline":                 merged.get("deadline"),
            "rolling":                  merged.get("rolling"),
            "is_annual":                merged.get("is_annual"),
            "application_url":          merged.get("application_url"),
            "application_url_verified": app_url_verified,
            "key_requirements":         merged.get("key_requirements"),
            "data_quality_score":       merged.get("data_quality_score"),
            "needs_review":             merged.get("needs_review"),
            "enriched_at":              datetime.utcnow().isoformat(),
        })
        db.commit()
    except Exception as exc:
        db.rollback()
        log.error(f"  P1 DB write error for id={row_id}: {exc}")
        raise
    finally:
        db.close()


# Pass 3: score recalculation and summary

def _run_pass3(touched_ids: list[int], min_score: float):
    """
    Recalculate data_quality_score for every row touched in this run
    and log a structured summary.
    """
    if not touched_ids:
        return

    db = database.SessionLocal()
    try:
        placeholders = ",".join(str(i) for i in touched_ids)
        rows = db.execute(
            text(f"SELECT * FROM scraped_grants WHERE id IN ({placeholders})")
        ).mappings().fetchall()

        still_incomplete = []
        for row in rows:
            row_dict = dict(row)
            new_score = recalculate_score({
                **row_dict,
                "tags":           json.loads(row_dict.get("tags") or "[]"),
                "areas_of_focus": json.loads(row_dict.get("areas_of_focus") or "[]"),
            })
            # Only update if score drifted (e.g. from schema weight change)
            if abs((row_dict.get("data_quality_score") or 0) - new_score) > 0.001:
                db.execute(
                    text("UPDATE scraped_grants SET data_quality_score = :s WHERE id = :id"),
                    {"s": new_score, "id": row_dict["id"]},
                )
            if not row_dict.get("application_url_verified") or not row_dict.get("deadline"):
                still_incomplete.append(row_dict)

        db.commit()
    finally:
        db.close()

    return still_incomplete



def main(
    dry_run: bool = False,
    limit: int = None,
    min_score: float = TARGET_SCORE,
    force: bool = False,
    skip_pass2: bool = False,
    pass2_limit: int = PASS2_LIMIT_DEFAULT,
):
    if not dry_run:
        client, deploy = build_client()
    else:
        client, deploy = None, None
        log.info("DRY-RUN mode — no DB writes, no API calls")

    url_index, title_index = build_text_index()

    db = database.SessionLocal()
    try:
        if force:
            query = "SELECT * FROM scraped_grants WHERE data_quality_score < :s ORDER BY data_quality_score DESC"
            log.info("--force: re-enriching all grants below score threshold (ignoring enriched_at)")
        else:
            query = "SELECT * FROM scraped_grants WHERE data_quality_score < :s AND enriched_at IS NULL ORDER BY data_quality_score DESC"
        rows = db.execute(text(query), {"s": min_score}).mappings().fetchall()
    finally:
        db.close()

    rows = list(rows)
    if limit:
        rows = rows[:limit]

    total           = len(rows)
    p1_enriched     = 0
    p1_no_text      = 0
    p1_ai_fail      = 0
    score_before    = [r["data_quality_score"] or 0 for r in rows]
    p1_touched_ids  = []

    errors_by_type = {
        "no_text":    0,
        "ai_timeout": 0,
        "json_parse": 0,
        "db_write":   0,
        "other":      0,
    }

    log.info(f"Pass 1 — grants to enrich: {total}  "
             f"(score < {min_score}"
             f"{', force=True' if force else ', incremental'})")

    if total == 0:
        print("\nNothing to enrich — all grants are above threshold or already enriched.")
        print("Use --force to re-enrich everything.")
        return

    print(f"\n{'─'*60}")
    print(f"PASS 1 — Extract from cached scraped text ({total} grants)")
    print(f"{'─'*60}")

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(_pass1_enrich_one, row, client, deploy, url_index, title_index, dry_run): i
            for i, row in enumerate(rows, 1)
        }
        for future in as_completed(futures):
            i = futures[future]
            try:
                row_id, merged, status, old_score = future.result()
            except Exception as exc:
                log.error(f"  Unexpected error on future {i}: {type(exc).__name__}: {exc}")
                p1_ai_fail += 1
                errors_by_type["other"] += 1
                continue

            if status == "no_text":
                log.warning(f"  [{i}/{total}] No combined_text — skipping")
                p1_no_text += 1
                errors_by_type["no_text"] += 1
            elif status == "ai_fail":
                p1_ai_fail += 1
                errors_by_type["other"] += 1
            elif status == "dry_run":
                p1_enriched += 1
            elif status == "ok":
                try:
                    _write_pass1_result(row_id, merged)
                    new_score = merged.get("data_quality_score", 0)
                    log.info(f"  [{i}/{total}] Score: {old_score:.2f} → {new_score:.2f}")
                    p1_enriched += 1
                    p1_touched_ids.append(row_id)
                except Exception as exc:
                    log.error(f"  DB write failed for id={row_id}: {type(exc).__name__}: {exc}")
                    p1_ai_fail += 1
                    errors_by_type["db_write"] += 1

    p2_candidates  = 0
    p2_improved    = 0
    p2_touched_ids = []

    if skip_pass2:
        log.info("Pass 2 skipped (--skip-pass2)")
    else:
        # Find rows where apply URL still unverified OR deadline still missing
        db = database.SessionLocal()
        try:
            p2_rows = db.execute(text("""
                SELECT * FROM scraped_grants
                WHERE enriched_at IS NOT NULL
                  AND (
                    application_url_verified = 0
                    OR application_url_verified IS NULL
                    OR (deadline IS NULL AND (rolling IS NULL OR rolling = FALSE))
                  )
                ORDER BY data_quality_score DESC
            """)).mappings().fetchall()
        finally:
            db.close()

        p2_rows = list(p2_rows)[:pass2_limit]
        p2_candidates = len(p2_rows)

        print(f"\n{'─'*60}")
        print(f"PASS 2 — Deep link extraction ({p2_candidates} candidates, limit={pass2_limit})")
        print(f"{'─'*60}")

        if p2_candidates == 0:
            log.info("  Pass 2: no candidates found — all rows have verified apply URLs and deadlines")
        else:
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = {
                    pool.submit(_pass2_enrich_one, row, client, deploy, dry_run): i
                    for i, row in enumerate(p2_rows, 1)
                }
                for future in as_completed(futures):
                    i = futures[future]
                    try:
                        row_id, updates, improved = future.result()
                    except Exception as exc:
                        log.error(f"  P2 unexpected error on future {i}: {exc}")
                        continue

                    if improved and updates:
                        try:
                            row_dict = dict(p2_rows[i - 1])
                            _write_pass2_result(row_id, updates, row_dict)
                            found = []
                            if updates.get("application_url"):
                                found.append(f"apply_url={updates['application_url'][:60]}")
                            if updates.get("deadline"):
                                found.append(f"deadline={updates['deadline']}")
                            log.info(f"  P2 [{i}/{p2_candidates}] improved — {', '.join(found)}")
                            p2_improved += 1
                            p2_touched_ids.append(row_id)
                        except Exception:
                            pass
                    else:
                        log.info(f"  P2 [{i}/{p2_candidates}] no improvement")

    # ── Pass 3: score recalculation + final report ───────────
    print(f"\n{'─'*60}")
    print(f"PASS 3 — Score recalculation")
    print(f"{'─'*60}")

    all_touched = list(set(p1_touched_ids + p2_touched_ids))
    still_incomplete = _run_pass3(all_touched, min_score) or []
    log.info(f"  Recalculated scores for {len(all_touched)} touched rows")

    total_errors = sum(errors_by_type.values())

    print(f"\n{'='*60}")
    print(f"ENRICHMENT COMPLETE")
    print(f"{'='*60}")
    print(f"Pass 1 — processed:        {total}")
    print(f"  Enriched:                {p1_enriched}")
    print(f"  Skipped (no text):       {p1_no_text}")
    print(f"  Skipped (AI fail):       {p1_ai_fail}")
    print(f"Pass 2 — candidates:       {p2_candidates}")
    print(f"  Improved by Pass 2:      {p2_improved}")
    print(f"  Still incomplete:        {len(still_incomplete)}")
    print(f"Pass 3 — scores refreshed: {len(all_touched)}")

    if total_errors > 0:
        print(f"\n{'─'*60}")
        print(f"ERROR BREAKDOWN ({total_errors} total)")
        print(f"{'─'*60}")
        labels = {
            "no_text":    "No scraped text found",
            "ai_timeout": "Azure AI timeout",
            "json_parse": "AI returned invalid JSON",
            "db_write":   "Database write failed",
            "other":      "Other / unexpected",
        }
        for key, count in errors_by_type.items():
            if count > 0:
                print(f"  {labels[key]:<28} {count}")

    if not dry_run and p1_enriched > 0:
        db = database.SessionLocal()
        try:
            rows_after = db.execute(
                text("SELECT data_quality_score, title, state FROM scraped_grants ORDER BY data_quality_score DESC")
            ).fetchall()
            scores      = [r[0] or 0 for r in rows_after]
            above_target = sum(1 for s in scores if s >= min_score)
            avg         = sum(scores) / len(scores) if scores else 0
            avg_before  = sum(score_before) / len(score_before) if score_before else 0
            print(f"\nAverage score: {avg_before:.2f} → {avg:.2f}")
            print(f"Grants >= {min_score}: {above_target} / {len(scores)}")
            print(f"\nTop 10 by score:")
            print(f"  {'Score':>6}  {'State':>5}  Title")
            print(f"  {'-'*6}  {'-'*5}  {'-'*50}")
            for score, title, state in rows_after[:10]:
                print(f"  {score or 0:>6.2f}  {state or '?':>5}  {(title or '')[:55]}")
        finally:
            db.close()

    if still_incomplete:
        print(f"\nStill incomplete ({len(still_incomplete)} rows — no verified URL or deadline):")
        for r in still_incomplete[:10]:
            print(f"  [{r.get('state','?')}] {(r.get('title') or '')[:60]}")
        if len(still_incomplete) > 10:
            print(f"  ... and {len(still_incomplete) - 10} more")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Multi-pass enrichment using Azure OpenAI")
    parser.add_argument("--dry-run",      action="store_true", help="Preview without API calls or DB writes")
    parser.add_argument("--limit",        type=int,   default=None,               help="Only process first N grants in Pass 1")
    parser.add_argument("--min-score",    type=float, default=TARGET_SCORE,       help=f"Enrich grants below this score (default {TARGET_SCORE})")
    parser.add_argument("--force",        action="store_true",                    help="Re-enrich all grants, ignoring enriched_at")
    parser.add_argument("--skip-pass2",   action="store_true",                    help="Skip deep link extraction (Pass 2)")
    parser.add_argument("--pass2-limit",  type=int,   default=PASS2_LIMIT_DEFAULT, help=f"Max rows for Pass 2 (default {PASS2_LIMIT_DEFAULT})")
    args = parser.parse_args()

    main(
        dry_run    = args.dry_run,
        limit      = args.limit,
        min_score  = args.min_score,
        force      = args.force,
        skip_pass2 = args.skip_pass2,
        pass2_limit= args.pass2_limit,
    )
