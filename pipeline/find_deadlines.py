#!/usr/bin/env python3
"""
find_deadlines.py

Three-layer deadline finder for scraped_grants rows that have no deadline:

  Layer 1 — Regex on stripped combined_text
            Strip boilerplate (nav/footer/cookie banners), then look for
            explicit deadline keywords and date patterns.

  Layer 2 — Live re-fetch of the opportunity_url
            Fetch the grant page fresh, parse with BeautifulSoup, target
            sections that typically contain deadlines (h2/h3 near "deadline",
            "how to apply", "application timeline").

  Layer 3 — Azure OpenAI deadline-only prompt
            Send stripped body text to the model with a focused prompt.
            If no date found, determine whether rolling=True applies.

Run from the project root:
    python find_deadlines.py

Optional flags:
    --dry-run      Show what would be done without writing to DB
    --limit N      Only process first N grants (testing)
    --skip-fetch   Skip Layer 2 (no HTTP requests, useful offline)
    --skip-ai      Skip Layer 3
"""

import sys
import os
import re
import json
import time
import argparse
import logging
import requests
from datetime import datetime, date
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent   # pipeline/../ = project root
APP_DIR      = PROJECT_ROOT / "app"
DATA_ROOT    = PROJECT_ROOT / "data"

sys.path.insert(0, str(APP_DIR))
os.chdir(str(APP_DIR))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from bs4 import BeautifulSoup
import database
from sqlalchemy import text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

TODAY    = date.today()
CUR_YEAR = TODAY.year
NXT_YEAR = CUR_YEAR + 1

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
}

# ── Boilerplate patterns to strip from combined_text ─────────────────────────
# These are nav/footer chunks that appear on every PA DCED page and confuse
# date extraction.
BOILERPLATE_PATTERNS = [
    # PA DCED full nav block
    r"Why PA\s+Pennsylvania Profile.*?Compare Communities",
    # Footer copyright block
    r"©\s*\d{4}\s+Commonwealth of Pennsylvania.*?Back To Top",
    # Cookie consent
    r"By clicking the continue button.*?CONTINUE",
    # Newsletter signup
    r'"\s*\*\s*"\s+indicates required fields.*?Newsletter Sign Up Confirmation',
    # Social media links block
    r"Feeling Social\? Follow Us!.*?Newsletter",
    # Generic nav repeats
    r"About Us\s+Library\s+Translate\s+Facebook.*?Newsletter\s+Search",
    # Repeated "Most Viewed Programs" sidebar
    r"Most Viewed Programs\s+Educational Improvement.*?And many more\.\.\.",
    # "Interested in doing business" footer CTA
    r"Interested in doing business in Pennsylvania\?.*?Check Now",
    # DC.gov footer
    r"Follow Us on X\s+Facebook\s+Mobile.*?About DC\.Gov",
    # NY ESD boilerplate
    r"Empire State Development.*?Privacy Policy",
]

BOILERPLATE_RE = re.compile(
    "|".join(BOILERPLATE_PATTERNS),
    re.IGNORECASE | re.DOTALL,
)

# ── Date extraction helpers ───────────────────────────────────────────────────

MONTH_NAMES = (
    "january|february|march|april|may|june|july|august|"
    "september|october|november|december|"
    "jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
)

# Patterns ordered by specificity (most precise first)
DATE_PATTERNS = [
    # MM/DD/YYYY
    (r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", "slash"),
    # Month DD, YYYY  or  Month DD YYYY
    (rf"\b({MONTH_NAMES})[\s.]+(\d{{1,2}}),?\s+(\d{{4}})\b", "month_long"),
    # DD Month YYYY
    (rf"\b(\d{{1,2}})\s+({MONTH_NAMES})\s+(\d{{4}})\b", "day_month"),
    # Month DD (no year — infer year)
    (rf"\b({MONTH_NAMES})[\s.]+(\d{{1,2}})\b(?!\s*,?\s*\d{{4}})", "month_no_year"),
    # YYYY-MM-DD
    (r"\b(\d{4})-(\d{2})-(\d{2})\b", "iso"),
]

# Keywords that, when within 120 chars of a date, signal it's a deadline
DEADLINE_KEYWORDS = re.compile(
    r"(deadline|due\s+date|apply\s+by|submit\s+by|applications?\s+due|"
    r"closes?\s+on|close\s+date|open\s+through|open\s+until|"
    r"last\s+day\s+to|must\s+be\s+(submitted|received)\s+by|"
    r"application\s+period\s+ends?|grant\s+cycle\s+ends?)",
    re.IGNORECASE,
)

# Signals that a grant is rolling / has no fixed deadline
ROLLING_SIGNALS = re.compile(
    r"(rolling\s+(basis|deadline|application)|"
    r"continuous(ly)?\s+(accept|open)|"
    r"first.come[\s,]+first.served|"
    r"open\s+year.round|"
    r"no\s+deadline|"
    r"until\s+funds?\s+(are\s+)?exhausted|"
    r"applications?\s+(are\s+)?accepted\s+at\s+any\s+time|"
    r"ongoing\s+(program|funding|basis))",
    re.IGNORECASE,
)

# Signals that a program recurs annually but has no fixed date on the page
# → set rolling=True, is_annual=True (deadline set per-cycle via RFP)
ANNUAL_SIGNALS = re.compile(
    r"(each\s+year\s+.{0,40}(provides?|awards?|funds?|distributes?)|"
    r"annual(ly)?\s+(entitlement|formula|funding|allocation|grant\s+cycle)|"
    r"entitlement\s+(program|funding|grant).{0,60}annual|"
    r"formula\s+(grant|fund).{0,30}annual|"
    r"awarded\s+annually|"
    r"distributed\s+annually|"
    r"recurring\s+(annual|grant)|"
    r"fiscal\s+year\s+(allocation|award|funding)|"
    r"(january|february|march|april|may|june|july|august|september|october|november|december)\s+of\s+each\s+year)",
    re.IGNORECASE,
)

# Signals that a program is a pass-through / locally-administered service
# with no central application deadline — rolling by design
PASSTHROUGH_SIGNALS = re.compile(
    r"(find\s+(the\s+)?(local|nearest|your)\s+.{0,30}(agency|office|center|provider|network)|"
    r"contact\s+your\s+(local|county|regional)\s+.{0,20}(office|agency|program)|"
    r"administered\s+(by|through)\s+(local|county|regional|participating)|"
    r"apply\s+(directly|through)\s+(your\s+local|a\s+local|the\s+local)|"
    r"(local|county|community)\s+(agency|organization|partner)\s+.{0,30}(administers?|manages?|delivers?)|"
    r"services?\s+(are\s+)?(provided|delivered|available)\s+(through|via)\s+(local|county)|"
    r"serves?\s+your\s+(county|area|region|community))",
    re.IGNORECASE,
)

# Footer noise date (same on every PA DCED page — ignore it)
FOOTER_NOISE_RE = re.compile(
    r"February\s+25,?\s+2026|"
    r"©\s*\d{4}\s+Commonwealth",
    re.IGNORECASE,
)

MONTH_MAP = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}


def parse_to_date(match_tuple, pattern_type):
    """Convert a regex match group tuple into a date object or None."""
    try:
        if pattern_type == "slash":
            m, d_, y = int(match_tuple[0]), int(match_tuple[1]), int(match_tuple[2])
            return date(y, m, d_)
        elif pattern_type == "month_long":
            mon, d_, y = match_tuple[0].lower(), int(match_tuple[1]), int(match_tuple[2])
            return date(y, MONTH_MAP[mon], d_)
        elif pattern_type == "day_month":
            d_, mon, y = int(match_tuple[0]), match_tuple[1].lower(), int(match_tuple[2])
            return date(y, MONTH_MAP[mon], d_)
        elif pattern_type == "month_no_year":
            mon, d_ = match_tuple[0].lower(), int(match_tuple[1])
            m_num = MONTH_MAP[mon]
            # Use current year; if already past, use next year
            candidate = date(CUR_YEAR, m_num, d_)
            if candidate < TODAY:
                candidate = date(NXT_YEAR, m_num, d_)
            return candidate
        elif pattern_type == "iso":
            y, m, d_ = int(match_tuple[0]), int(match_tuple[1]), int(match_tuple[2])
            return date(y, m, d_)
    except (ValueError, KeyError):
        return None


def is_footer_noise(text_around):
    """True if this date hit is just the PA footer date."""
    return bool(FOOTER_NOISE_RE.search(text_around))


def extract_dates_from_text(text):
    """
    Return list of (date_obj, confidence) tuples found in text.
    confidence = 'high' if near a deadline keyword, else 'low'.
    Ignores known footer noise.
    """
    results = []
    for pattern, ptype in DATE_PATTERNS:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            start = max(0, m.start() - 120)
            end   = min(len(text), m.end() + 120)
            context = text[start:end]

            if is_footer_noise(context):
                continue

            d_obj = parse_to_date(m.groups(), ptype)
            if d_obj is None:
                continue
            if d_obj.year < 2024 or d_obj.year > 2030:
                continue  # implausible grant deadline

            confidence = "high" if DEADLINE_KEYWORDS.search(context) else "low"
            results.append((d_obj, confidence, context.strip()))

    # Sort: high-confidence first, then soonest
    results.sort(key=lambda x: (0 if x[1] == "high" else 1, x[0]))
    return results


# ── Layer 1: Regex on stripped combined_text ──────────────────────────────────

def build_text_index():
    """Returns {app_url_lower: (combined_text, source_url, state), ...}
    Auto-discovers all *_raw.json files across data/dc/, data/md/, data/ny/, data/pa/.
    """
    url_idx, title_idx = {}, {}
    state_dirs = [DATA_ROOT / s for s in ("dc", "md", "ny", "pa")]
    for state_dir in state_dirs:
        if not state_dir.exists():
            continue
        for path in sorted(state_dir.glob("*_raw.json")):
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
                grants = data.get("grants", [])
                for g in grants:
                    ct  = g.get("combined_text") or ""
                    url = (g.get("application_url") or "").strip().lower()
                    if url:
                        url_idx[url] = (ct, g.get("source_url", ""), g.get("state", ""))
                    title = (g.get("title") or "").strip().lower()
                    if title:
                        title_idx[title] = (ct, g.get("source_url", ""), g.get("state", ""))
            except Exception as exc:
                log.warning(f"Could not read {path.name}: {exc}")
    return url_idx, title_idx


def layer1_regex(combined_text):
    """
    Strip boilerplate, then:
      1. Check for rolling signals
      2. Check for annual-program signals (rolling + is_annual)
      3. Run date regex
    Returns (deadline_str, rolling, is_annual).
    """
    clean = BOILERPLATE_RE.sub(" ", combined_text)
    clean = re.sub(r"\s{3,}", "\n", clean)

    if ROLLING_SIGNALS.search(clean):
        return None, True, False

    if ANNUAL_SIGNALS.search(clean):
        log.info("    L1 annual-program signal found → rolling+annual")
        return None, True, True

    if PASSTHROUGH_SIGNALS.search(clean):
        log.info("    L1 pass-through program signal → rolling")
        return None, True, False

    hits = extract_dates_from_text(clean)
    if not hits:
        return None, False, False

    high = [h for h in hits if h[1] == "high"]
    chosen = high[0] if high else hits[0]
    dl = chosen[0].strftime("%m/%d/%Y")
    log.info(f"    L1 regex found: {dl} (conf={chosen[1]}) — context: {chosen[2][:80]!r}")
    return dl, False, False


# ── Layer 2: Live page re-fetch ───────────────────────────────────────────────

DEADLINE_SECTION_SELECTORS = [
    # Common heading labels that precede deadline info
    "h2", "h3", "h4", "strong", "b", "dt", "th",
]

def layer2_fetch(url):
    """
    Fetch the URL, parse the HTML, extract text near deadline keywords.
    Returns (deadline_str, rolling, is_annual) or (None, None, None).
    """
    if not url or not url.startswith("http"):
        return None, None, None
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as exc:
        log.warning(f"    L2 fetch failed: {exc}")
        return None, None, None

    soup = BeautifulSoup(resp.text, "html.parser")

    for tag in soup.select("nav, footer, header, .menu, .nav, #menu, #footer, .cookie, .sidebar, script, style"):
        tag.decompose()

    body_text = soup.get_text(separator="\n")

    # Build focused text from sections near deadline keywords
    focused_parts = []
    for sel in DEADLINE_SECTION_SELECTORS:
        for el in soup.find_all(sel):
            el_text = el.get_text(strip=True)
            if DEADLINE_KEYWORDS.search(el_text):
                chunk = el_text
                sib = el.find_next_sibling()
                for _ in range(3):
                    if sib:
                        chunk += " " + sib.get_text(separator=" ", strip=True)
                        sib = sib.find_next_sibling()
                focused_parts.append(chunk)

    focused_text = "\n".join(focused_parts) if focused_parts else body_text

    if ROLLING_SIGNALS.search(focused_text) or ROLLING_SIGNALS.search(body_text):
        log.info("    L2 fetch: rolling signal found")
        return None, True, False

    if ANNUAL_SIGNALS.search(body_text):
        log.info("    L2 fetch: annual-program signal found → rolling+annual")
        return None, True, True

    if PASSTHROUGH_SIGNALS.search(body_text):
        log.info("    L2 fetch: pass-through program signal → rolling")
        return None, True, False

    hits = extract_dates_from_text(focused_text)
    if not hits:
        hits = extract_dates_from_text(body_text)

    if hits:
        high = [h for h in hits if h[1] == "high"]
        chosen = high[0] if high else hits[0]
        dl = chosen[0].strftime("%m/%d/%Y")
        log.info(f"    L2 fetch found: {dl} (conf={chosen[1]})")
        return dl, False, False

    return None, None, None


# ── Layer 3: Azure OpenAI deadline-only prompt ────────────────────────────────

DEADLINE_PROMPT = """
You are a grant research assistant. Your ONLY job is to find the application DEADLINE.

Read the text below and return a single JSON object:
{{
  "deadline":      "MM/DD/YYYY or null",
  "rolling":       true or false,
  "is_annual":     true or false,
  "deadline_note": "one sentence explaining what you found or why there is no deadline"
}}

RULES:
- deadline: the date applicants must SUBMIT their application. MM/DD/YYYY format.
  If only month + day found with no year, use {CUR_YEAR} if not yet passed, else {NXT_YEAR}.
- rolling: true if the grant explicitly says "rolling basis", "first-come first-served",
  "open year-round", "no deadline", "until funds exhausted", or similar.
- is_annual: true if the grant repeats every year on a similar date.
- If truly no deadline information exists, return deadline=null, rolling=false.
- IGNORE any page footer dates, copyright dates, or news article dates.
- Return ONLY valid JSON. No markdown, no explanation outside the JSON.

State context: {STATE}
Source: {URL}

TEXT:
{TEXT}
"""

def build_ai_client():
    from openai import AzureOpenAI
    api_key  = os.getenv("AZURE_OPENAI_API_KEY")
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    deploy   = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
    if not api_key or not endpoint:
        return None, None
    return AzureOpenAI(
        api_key=api_key,
        api_version="2024-02-01",
        azure_endpoint=endpoint,
    ), deploy


def layer3_ai(client, deploy, text_body, url, state):
    """Call Azure OpenAI with a focused deadline-only prompt."""
    # Strip boilerplate before sending
    clean = BOILERPLATE_RE.sub(" ", text_body)
    clean = re.sub(r"\s{3,}", "\n", clean)

    prompt = DEADLINE_PROMPT.format(
        CUR_YEAR=CUR_YEAR,
        NXT_YEAR=NXT_YEAR,
        STATE=state,
        URL=url,
        TEXT=clean[:6000],
    )
    try:
        resp = client.chat.completions.create(
            model=deploy,
            messages=[
                {"role": "system", "content": "Extract deadline from grant text. Return JSON only."},
                {"role": "user",   "content": prompt},
            ],
            temperature=0,
            max_tokens=300,
        )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r"^```json\s*", "", raw)
        raw = re.sub(r"```$", "", raw).strip()
        result = json.loads(raw)
        dl      = result.get("deadline")
        rolling = result.get("rolling", False)
        note    = result.get("deadline_note", "")
        log.info(f"    L3 AI: deadline={dl!r}, rolling={rolling} — {note[:80]}")
        return dl, rolling, result.get("is_annual", False)
    except Exception as exc:
        log.warning(f"    L3 AI error: {exc}")
        return None, None, None


# ── DB update ─────────────────────────────────────────────────────────────────

def update_row(row_id, deadline, rolling, is_annual, dry_run):
    if dry_run:
        log.info(f"    [DRY-RUN] Would set deadline={deadline!r}, rolling={rolling}, is_annual={is_annual}")
        return
    db = database.SessionLocal()
    try:
        db.execute(text("""
            UPDATE scraped_grants
               SET deadline  = :deadline,
                   rolling   = :rolling,
                   is_annual = :is_annual
             WHERE id = :id
        """), {"id": row_id, "deadline": deadline, "rolling": rolling, "is_annual": is_annual})
        db.commit()
    except Exception as exc:
        db.rollback()
        log.error(f"    DB error: {exc}")
    finally:
        db.close()


# ── Main ──────────────────────────────────────────────────────────────────────

def main(dry_run=False, limit=None, skip_fetch=False, skip_ai=False):
    # Fetch rows with no deadline
    db = database.SessionLocal()
    try:
        rows = db.execute(
            text("SELECT id, title, state, application_url, opportunity_url FROM scraped_grants WHERE deadline IS NULL ORDER BY id")
        ).fetchall()
    finally:
        db.close()

    if limit:
        rows = rows[:limit]

    log.info(f"Grants with no deadline: {len(rows)}")

    url_idx, title_idx = build_text_index()

    ai_client, ai_deploy = (None, None) if skip_ai else build_ai_client()
    if not skip_ai and ai_client is None:
        log.warning("Azure OpenAI not configured (AZURE_OPENAI_API_KEY missing) — Layer 3 will be skipped")

    stats = {"l1": 0, "l2": 0, "l3": 0, "rolling": 0, "no_deadline": 0}

    for i, row in enumerate(rows, 1):
        row_id  = row[0]
        title   = row[1] or ""
        state   = row[2] or ""
        app_url = (row[3] or row[4] or "").strip()
        log.info(f"[{i}/{len(rows)}] {state} | {title[:60]}")

        deadline  = None
        rolling   = False
        is_annual = False
        layer_used = None

        # ── Layer 1: regex on stored combined_text ────────────────────────────
        entry = url_idx.get(app_url.lower()) or title_idx.get(title.lower())
        if entry:
            ct, src_url, _ = entry
            deadline, rolling, is_annual = layer1_regex(ct)
            if deadline:
                layer_used = "L1-regex"
                stats["l1"] += 1
            elif rolling:
                layer_used = "L1-rolling" if not is_annual else "L1-annual"
                stats["rolling"] += 1

        # ── Layer 2: live re-fetch ─────────────────────────────────────────────
        if not deadline and not rolling and not skip_fetch:
            log.info("    L1 found nothing — trying live fetch …")
            dl2, roll2, ann2 = layer2_fetch(app_url)
            if dl2:
                deadline   = dl2
                layer_used = "L2-fetch"
                stats["l2"] += 1
            elif roll2:
                rolling    = roll2
                is_annual  = bool(ann2)
                layer_used = "L2-rolling" if not ann2 else "L2-annual"
                stats["rolling"] += 1

        # ── Layer 3: AI deadline-only prompt ──────────────────────────────────
        if not deadline and not rolling and not skip_ai and ai_client:
            log.info("    L2 found nothing — trying AI …")
            # Use freshly-fetched body if available, else fall back to stored text
            body = (entry[0] if entry else "") or ""
            ai_dl, ai_rolling, ai_annual = layer3_ai(ai_client, ai_deploy, body, app_url, state)
            if ai_dl:
                deadline  = ai_dl
                is_annual = bool(ai_annual)
                layer_used = "L3-ai"
                stats["l3"] += 1
            elif ai_rolling:
                rolling = True
                layer_used = "L3-rolling"
                stats["rolling"] += 1
            time.sleep(1)   # rate-limit Layer 3 calls

        if not deadline and not rolling:
            log.info("    All layers exhausted — no deadline found")
            stats["no_deadline"] += 1

        if layer_used:
            log.info(f"    → {layer_used}: deadline={deadline!r}, rolling={rolling}")

        # Write result even if just rolling=True (important signal for the UI)
        if deadline or rolling:
            update_row(row_id, deadline, rolling, is_annual, dry_run)

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("DEADLINE SEARCH COMPLETE")
    print(f"{'='*60}")
    print(f"Total processed:          {len(rows)}")
    print(f"Found via L1 regex:       {stats['l1']}")
    print(f"Found via L2 fetch:       {stats['l2']}")
    print(f"Found via L3 AI:          {stats['l3']}")
    print(f"Marked rolling (no date): {stats['rolling']}")
    print(f"Still no deadline:        {stats['no_deadline']}")

    if not dry_run:
        db = database.SessionLocal()
        try:
            total       = db.execute(text("SELECT COUNT(*) FROM scraped_grants")).scalar()
            with_dl     = db.execute(text("SELECT COUNT(*) FROM scraped_grants WHERE deadline IS NOT NULL")).scalar()
            rolling_cnt = db.execute(text("SELECT COUNT(*) FROM scraped_grants WHERE rolling = 1 AND deadline IS NULL")).scalar()
            print(f"\nDB state after run:")
            print(f"  Total grants:        {total}")
            print(f"  Have deadline:       {with_dl}")
            print(f"  Rolling (no date):   {rolling_cnt}")
            print(f"  Still unknown:       {total - with_dl - rolling_cnt}")
        finally:
            db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Three-layer deadline finder for scraped_grants")
    parser.add_argument("--dry-run",    action="store_true", help="No DB writes")
    parser.add_argument("--limit",      type=int, default=None, help="Process first N grants only")
    parser.add_argument("--skip-fetch", action="store_true", help="Skip Layer 2 (no HTTP requests)")
    parser.add_argument("--skip-ai",    action="store_true", help="Skip Layer 3 (no AI calls)")
    args = parser.parse_args()
    main(dry_run=args.dry_run, limit=args.limit, skip_fetch=args.skip_fetch, skip_ai=args.skip_ai)
