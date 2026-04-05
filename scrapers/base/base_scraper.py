"""
Base scraper utilities shared by all state scrapers.
Provides date/amount extraction, PDF handling, Azure AI enrichment, and DB loading.
"""

import re
import io
import os
import sys
import json
import time
import logging
import threading
import hashlib
import requests
import PyPDF2
from bs4 import BeautifulSoup
from datetime import datetime
from dateutil import parser as dateutil_parser
from typing import Optional, Dict, List, Any
from openai import AzureOpenAI
from pathlib import Path

# Load .env from project root so Azure credentials are available
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
except ImportError:
    pass  # python-dotenv not installed; rely on shell environment

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # scrapers/base/../../.. = project root
_CACHE_PATH   = _PROJECT_ROOT / "data" / "scrape_cache.json"

class ScraperCache:
    """
    URL-keyed cache. Stores content hash + extracted grant data.
    On day 2+, unchanged pages are returned from cache — skipping PDF
    download and AI calls entirely.
    Thread-safe via a single lock.
    """
    def __init__(self, path: Path):
        self.path  = path
        self._lock = threading.Lock()
        self._data: Dict[str, Any] = {}
        self._dirty = False
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                with open(self.path) as f:
                    self._data = json.load(f)
                log.info(f"ScraperCache loaded — {len(self._data)} entries from {self.path}")
            except Exception:
                self._data = {}

    def flush(self):
        """Write accumulated changes to disk."""
        with self._lock:
            if not self._dirty:
                return
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "w") as f:
                    json.dump(self._data, f)
                self._dirty = False
            except Exception as e:
                log.warning(f"Cache flush failed: {e}")

    @staticmethod
    def content_hash(content: bytes) -> str:
        return hashlib.md5(content).hexdigest()

    def get_if_fresh(self, url: str, content_hash: str) -> Optional[Dict]:
        """Return cached grant if URL exists and content hash matches, else None."""
        with self._lock:
            entry = self._data.get(url)
            if entry and entry.get("hash") == content_hash and entry.get("grant"):
                return entry["grant"]
        return None

    def set(self, url: str, content_hash: str, grant: Dict):
        with self._lock:
            self._data[url] = {
                "hash":       content_hash,
                "scraped_at": datetime.utcnow().isoformat(),
                "grant":      grant,
            }
            self._dirty = True


# Module-level singletons — shared across all scraper threads
_cache    = ScraperCache(_CACHE_PATH)
_AI_SEM   = threading.Semaphore(3)   # max 3 concurrent Azure OpenAI calls
_HTTP_SEM = threading.Semaphore(10)  # max 10 concurrent outbound HTTP requests

TODAY      = datetime.today()
THIS_YEAR  = TODAY.year
NEXT_YEAR  = THIS_YEAR + 1

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

MIN_PDF_CHARS   = 300    # PDFs with less text than this are blank forms — skip them
MAX_PDF_PAGES   = 15     # Only read first N pages of a PDF
AI_TEXT_LIMIT   = 14000  # Max chars sent to Azure AI per call
REQUEST_TIMEOUT     = 10   # seconds — detail page fetches
PDF_REQUEST_TIMEOUT = 30   # seconds — kept higher for large PDF downloads
REQUEST_DELAY   = 1.2    # Seconds between requests — be polite to servers


def clean_ordinal(text: str) -> str:
    """
    Remove ordinal suffixes so dateutil can parse them.
    '31st of May'  →  '31 May'
    '15th March'   →  '15 March'
    """
    text = re.sub(r"(\d+)(st|nd|rd|th)\s+of\s+", r"\1 ", text, flags=re.IGNORECASE)
    text = re.sub(r"(\d+)(st|nd|rd|th)",           r"\1",   text, flags=re.IGNORECASE)
    return text.strip()


def is_expiring_soon(dt: datetime, days: int = 7) -> bool:
    """True if deadline is within `days` days from today."""
    gap = (dt.date() - TODAY.date()).days
    return 0 <= gap <= days


def try_parse(text: str) -> Optional[datetime]:
    """
    Try to parse a date string.
    Returns datetime or None.
    Only accepts years between 2025 and 2030 to avoid garbage.
    """
    try:
        dt = dateutil_parser.parse(text, fuzzy=True)
        if 2025 <= dt.year <= 2030:
            return dt
    except Exception:
        pass
    return None


def resolve_year(month_day_text: str) -> Optional[Dict]:
    """
    Given a date string with NO year (e.g. 'May 31', '15 March'),
    decide which year to use:
      - If the date has NOT passed yet this year  →  use THIS_YEAR
      - If the date HAS already passed this year  →  use NEXT_YEAR
      - If it is within 7 days                    →  flag for review
    """
    for year in [THIS_YEAR, NEXT_YEAR]:
        try:
            dt = dateutil_parser.parse(f"{month_day_text} {year}", fuzzy=True)
            if dt.year == year:
                if dt.date() >= TODAY.date():
                    return {
                        "deadline":     dt.strftime("%m/%d/%Y"),
                        "needs_review": is_expiring_soon(dt),
                    }
        except Exception:
            continue
    return None


def extract_date(text: str) -> Dict:
    """
    Master date extractor. Works through 5 layers:
      1. Rolling / open-ended language
      2. Annual / every-year patterns
      3. Trigger words (deadline, due, closes, etc.)
      4. Full dates anywhere in text
      5. Month + day only (no year) → apply year intelligence

    Returns a dict:
    {
        deadline:      '05/31/2026' or None,
        rolling:       True / False,
        is_annual:     True / False,
        confidence:    'high' / 'medium' / 'low',
        raw_text:      the snippet we found,
        needs_review:  True / False,
    }
    """
    result = {
        "deadline":     None,
        "rolling":      False,
        "is_annual":    False,
        "confidence":   "low",
        "raw_text":     None,
        "needs_review": False,
    }

    # Layer 1: rolling / open-ended
    rolling_phrases = [
        "rolling basis", "rolling deadline", "rolling admissions",
        "ongoing", "open-ended", "open ended", "no deadline",
        "open until further notice", "until funds are exhausted",
        "until funding runs out", "applications accepted anytime",
        "open now", "currently open", "no closing date",
    ]
    for phrase in rolling_phrases:
        if phrase in text.lower():
            result.update(rolling=True, confidence="high", raw_text=phrase)
            return result

    # Layer 2: annual / every-year patterns
    annual_patterns = [
        r"(\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?[A-Za-z]+)\s+every\s+year",
        r"([A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?)\s+every\s+year",
        r"every\s+year\s+(?:by\s+)?([A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?)",
        r"annual\s+deadline\s+(?:of\s+|is\s+)?([A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?)",
        r"annually\s+(?:on\s+|by\s+)?([A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?)",
        r"due\s+every\s+([A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?)",
        r"yearly\s+deadline\s*[:\-]?\s*([A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?)",
        r"(\d{1,2}(?:st|nd|rd|th)?\s+of\s+[A-Za-z]+)\s+(?:every|each)\s+year",
        r"each\s+year\s+(?:by\s+)?([A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?)",
        r"opens?\s+annually\s+(?:on\s+)?([A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?)",
        r"deadline\s+is\s+([A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?)\s+(?:of\s+)?each\s+year",
        r"([A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?)\s+(?:of\s+)?each\s+year",
    ]
    for pat in annual_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            raw = clean_ordinal(m.group(1).strip())
            resolved = resolve_year(raw)
            if resolved:
                result.update(
                    deadline=resolved["deadline"],
                    rolling=True,
                    is_annual=True,
                    confidence="high",
                    raw_text=m.group(0),
                    needs_review=resolved["needs_review"],
                )
                return result

    # Layer 3: trigger words (deadline, due, closes, etc.)
    trigger_patterns = [
        r"deadline[:\s\-]+([A-Za-z0-9,\s/\.]+?(?:\d{4}|\d{2}))",
        r"due\s+(?:date\s+)?[:\-]?\s*([A-Za-z0-9,\s/\.]+?(?:\d{4}|\d{2}))",
        r"applications?\s+due[:\s]+([A-Za-z0-9,\s/\.]+?(?:\d{4}|\d{2}))",
        r"submit(?:ted)?\s+by[:\s]+([A-Za-z0-9,\s/\.]+?(?:\d{4}|\d{2}))",
        r"clos(?:es?|ing)\s+(?:on\s+)?[:\-]?\s*([A-Za-z0-9,\s/\.]+?(?:\d{4}|\d{2}))",
        r"last\s+date[:\s]+([A-Za-z0-9,\s/\.]+?(?:\d{4}|\d{2}))",
        r"submission\s+deadline[:\s]+([A-Za-z0-9,\s/\.]+?(?:\d{4}|\d{2}))",
        r"apply\s+by[:\s]+([A-Za-z0-9,\s/\.]+?(?:\d{4}|\d{2}))",
        r"no\s+later\s+than[:\s]+([A-Za-z0-9,\s/\.]+?(?:\d{4}|\d{2}))",
        r"postmark(?:ed)?\s+by[:\s]+([A-Za-z0-9,\s/\.]+?(?:\d{4}|\d{2}))",
        r"received\s+by[:\s]+([A-Za-z0-9,\s/\.]+?(?:\d{4}|\d{2}))",
    ]
    for pat in trigger_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            raw = clean_ordinal(m.group(1).strip()[:60])
            dt  = try_parse(raw)
            if dt:
                result.update(
                    deadline=dt.strftime("%m/%d/%Y"),
                    confidence="high",
                    raw_text=m.group(0),
                    needs_review=is_expiring_soon(dt),
                )
                return result

    # Layer 4: any full date in text
    full_date_patterns = [
        r"\b(\d{1,2}/\d{1,2}/\d{4})\b",
        r"\b(\d{1,2}/\d{1,2}/\d{2})\b",
        r"\b([A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4})\b",
        r"\b(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+,?\s+\d{4})\b",
        r"\b([A-Za-z]+\s+\d{1,2},?\s+\d{4})\b",
        r"\b(\d{1,2}\s+[A-Za-z]+\s+\d{4})\b",
    ]
    candidates = []
    for pat in full_date_patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            raw = clean_ordinal(m.group(1))
            dt  = try_parse(raw)
            if dt and dt.date() >= TODAY.date():
                candidates.append({"dt": dt, "raw": m.group(0)})

    if candidates:
        candidates.sort(key=lambda x: x["dt"])
        best = candidates[0]
        result.update(
            deadline=best["dt"].strftime("%m/%d/%Y"),
            confidence="medium",
            raw_text=best["raw"],
            needs_review=is_expiring_soon(best["dt"]),
        )
        return result

    # Layer 5: month + day only, no year → infer year
    no_year_patterns = [
        r"\b([A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?)\b",
        r"\b(\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?[A-Za-z]+)\b",
        r"\b(\d{1,2}/\d{1,2})\b",
    ]
    for pat in no_year_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            raw      = clean_ordinal(m.group(1))
            resolved = resolve_year(raw)
            if resolved:
                result.update(
                    deadline=resolved["deadline"],
                    confidence="low",
                    raw_text=m.group(0),
                    needs_review=True,   # always review low-confidence
                )
                return result

    # Nothing found
    result["needs_review"] = True
    return result


def safe_multiply(match_str: str, multiplier: float) -> str:
    """
    Safely convert a matched number string to a full dollar amount.
    Returns original match unchanged if it cannot be parsed as a number.
    Handles edge cases like bare '.' or ',' that sneak through regex.
    """
    cleaned = match_str.replace(",", "").strip()
    # Must have at least one actual digit to be a valid number
    if not re.search(r"\d", cleaned):
        return match_str
    try:
        return f"${float(cleaned) * multiplier:.0f}"
    except (ValueError, TypeError):
        return match_str


def normalise_amount_text(text: str) -> str:
    """Convert shorthand like $1.5M or $2 million to full numbers."""
    text = re.sub(
        r"\$?([\d,\.]+)\s*million",
        lambda m: safe_multiply(m.group(1), 1_000_000),
        text, flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\$?([\d,\.]+)\s*M\b",
        lambda m: safe_multiply(m.group(1), 1_000_000),
        text, flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\$?([\d,\.]+)\s*k\b",
        lambda m: safe_multiply(m.group(1), 1_000),
        text, flags=re.IGNORECASE,
    )
    return text


def extract_amount(text: str) -> Dict:
    """
    Extract grant award amounts intelligently.
    Handles:
      - $50,000
      - $50,000 to $500,000
      - up to $1 million
      - between $10k and $50k
      - maximum award of $250,000
      - grants ranging from $5,000 to $25,000
      - $1,000,000 total program funding

    Returns:
    {
        award_min:      50000.0 or None,
        award_max:      500000.0 or None,
        total_funding:  None or float,
        award_text:     '$50,000 to $500,000',
    }
    """
    result = {
        "award_min":     None,
        "award_max":     None,
        "total_funding": None,
        "award_text":    None,
    }

    text = normalise_amount_text(text)

    def to_float(s: str) -> Optional[float]:
        try:
            cleaned = s.replace(",", "").replace("$", "").strip()
            if not cleaned or cleaned in (".", "-", ""):
                return None
            return float(cleaned)
        except (ValueError, TypeError):
            return None

    range_patterns = [
        r"\$([0-9,]+)\s+to\s+\$([0-9,]+)",
        r"\$([0-9,]+)\s*[-–—]\s*\$([0-9,]+)",
        r"between\s+\$([0-9,]+)\s+and\s+\$([0-9,]+)",
        r"from\s+\$([0-9,]+)\s+to\s+\$([0-9,]+)",
        r"ranging\s+from\s+\$([0-9,]+)\s+to\s+\$([0-9,]+)",
        r"minimum[:\s]+\$([0-9,]+).{0,30}maximum[:\s]+\$([0-9,]+)",
    ]
    for pat in range_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            mn = to_float(m.group(1))
            mx = to_float(m.group(2))
            if mn is not None and mx is not None:
                result["award_min"]  = mn
                result["award_max"]  = mx
                result["award_text"] = m.group(0)
                return result

    max_patterns = [
        r"up\s+to\s+\$([0-9,]+)",
        r"maximum\s+(?:award|grant|funding|amount)?\s*(?:of\s+)?\$([0-9,]+)",
        r"not\s+to\s+exceed\s+\$([0-9,]+)",
        r"award\s+(?:amount\s+)?up\s+to\s+\$([0-9,]+)",
        r"grants?\s+(?:of\s+)?up\s+to\s+\$([0-9,]+)",
        r"as\s+much\s+as\s+\$([0-9,]+)",
        r"max(?:imum)?\s*[:=]\s*\$([0-9,]+)",
    ]
    for pat in max_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            val = to_float(m.group(1))
            if val is not None:
                result["award_max"]  = val
                result["award_text"] = m.group(0)
                return result

    total_patterns = [
        r"total\s+(?:program\s+)?funding\s+(?:of\s+|available\s+)?\$([0-9,]+)",
        r"\$([0-9,]+)\s+(?:in\s+)?total\s+(?:program\s+)?funding",
        r"total\s+available\s+funds?\s*(?:of\s+|[:=])?\s*\$([0-9,]+)",
        r"program\s+has\s+\$([0-9,]+)\s+available",
    ]
    for pat in total_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            val = to_float(m.group(1))
            if val is not None:
                result["total_funding"] = val
                result["award_text"]    = m.group(0)
                return result

    amounts = re.findall(r"\$([0-9,]+)", text)
    if amounts:
        nums = []
        for a in amounts:
            v = to_float(a)
            if v is not None:
                nums.append(v)
        nums = sorted(set(n for n in nums if n >= 1000))
        if len(nums) >= 2:
            result["award_min"]  = nums[0]
            result["award_max"]  = nums[-1]
        elif len(nums) == 1:
            result["award_max"]  = nums[0]
        if nums:
            result["award_text"] = f"${nums[0]:,.0f}"

    return result


def safe_get(url: str, retries: int = 2,
             timeout: int = REQUEST_TIMEOUT) -> Optional[requests.Response]:
    """GET with retries and polite delay."""
    for attempt in range(retries):
        try:
            time.sleep(REQUEST_DELAY)
            with _HTTP_SEM:
                r = requests.get(url, headers=HEADERS, timeout=timeout)
            if r.status_code == 200:
                return r
            log.warning(f"  HTTP {r.status_code} for {url}")
        except Exception as e:
            log.warning(f"  Request error (attempt {attempt+1}): {e}")
    return None


def extract_pdf_text(url: str) -> Optional[str]:
    """
    Download a PDF and extract its text.
    Returns None if the PDF is a blank form or unreadable.
    """
    r = safe_get(url, timeout=PDF_REQUEST_TIMEOUT)
    if not r:
        return None
    try:
        reader = PyPDF2.PdfReader(io.BytesIO(r.content))
        pages  = min(MAX_PDF_PAGES, len(reader.pages))
        text   = "\n\n".join(
            reader.pages[i].extract_text() or ""
            for i in range(pages)
        )
        if len(text.strip()) < MIN_PDF_CHARS:
            log.info(f"  Skipping blank/form PDF: {url}")
            return None
        return text
    except Exception as e:
        log.warning(f"  PDF extract error: {e}")
        return None


def ai_extract(combined_text: str, source_url: str, state: str) -> Optional[Dict]:
    """
    Send combined HTML + PDF text to Azure OpenAI for final intelligent extraction.
    This catches everything the regex may have missed.
    """
    api_key  = os.getenv("AZURE_OPENAI_API_KEY")
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    deploy   = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")

    if not api_key or not endpoint:
        log.warning("Azure OpenAI credentials not set — skipping AI pass")
        return None

    client = AzureOpenAI(
        api_key=api_key,
        api_version="2024-02-01",
        azure_endpoint=endpoint,
    )

    prompt = f"""
You are an expert at reading government and foundation grant documents.

Analyze the text below and extract EVERY piece of information you can find.
Return ONLY valid JSON — no markdown, no explanation, just JSON.

{{
  "title":                    "Full official grant title",
  "summary":                  "1-2 sentence summary of what this grant funds",
  "description":              "4-6 sentence description including purpose, who benefits, and how to apply",
  "deadline":                 "MM/DD/YYYY or null",
  "rolling":                  true or false,
  "is_annual":                true or false (does this deadline repeat every year?),
  "posted_date":              "MM/DD/YYYY or null",
  "award_min":                number or null,
  "award_max":                number or null,
  "total_funding":            number or null,
  "award_text":               "e.g. up to $50,000 or $10,000 - $50,000",
  "eligibility_individual":   true or false,
  "eligibility_organization": true or false,
  "eligibility_notes":        "plain English description of who can apply",
  "eligible_applicant_types": ["list of applicant types"],
  "tags":                     ["3-6 relevant keyword tags"],
  "industry":                 "primary industry sector or null",
  "areas_of_focus":           ["Capital", "Networks", "Capacity Building", "Technical Assistance", "Mentorship", "Training"],
  "sdg_alignment":            ["e.g. SDG 8: Decent Work and Economic Growth"] or [],
  "contact_name":             "name or null",
  "contact_email":            "email or null",
  "contact_phone":            "phone or null",
  "opportunity_url":          "URL of the grant info page or null",
  "application_url":          "URL of the direct application form or null",
  "fee_required":             true or false,
  "fee_amount":               number or null,
  "equity_percentage":        true or false,
  "safe_note":                true or false,
  "logo_url":                 "logo URL if found or null",
  "sponsor_website":          "main website of the sponsoring agency or null",
  "key_requirements":         ["list of 3-5 key eligibility requirements"]
}}

RULES:
- Dates: convert ALL formats to MM/DD/YYYY. If only month+day found with no year,
  use {THIS_YEAR} if the date has not yet passed, else use {NEXT_YEAR}.
  If it says 'every year' or 'annually', set is_annual=true and rolling=true.
- Amounts: convert $1M to 1000000, $50k to 50000.
- If a field truly cannot be found, return null — never guess.
- opportunity_url is the page describing the grant.
- application_url is the direct form submission link (may be different).
- State context: {state}

TEXT TO ANALYZE (source: {source_url}):
{combined_text[:AI_TEXT_LIMIT]}
"""

    from openai import APITimeoutError
    try:
        with _AI_SEM:
            resp = client.chat.completions.create(
                model=deploy,
                messages=[
                    {"role": "system",  "content": "Extract structured grant data. Return valid JSON only."},
                    {"role": "user",    "content": prompt},
                ],
                temperature=0,
                max_tokens=2500,
                timeout=30,
            )
        raw = resp.choices[0].message.content
        # Strip markdown code fences if present
        raw = re.sub(r"^```json\s*", "", raw.strip())
        raw = re.sub(r"```$",        "", raw.strip())
        return json.loads(raw.strip())
    except APITimeoutError:
        log.warning(f"Azure AI timeout for {source_url} — skipping")
        return None
    except Exception as e:
        log.error(f"Azure AI error: {e}")
        return None


def calculate_quality_score(grant: Dict) -> float:
    """
    Score from 0.0 to 1.0 based on how many key fields are populated.
    Below 0.5 → needs_review = True automatically.

    Notes:
    - deadline and rolling share one 0.15 slot (either counts).
    - application_url only scores if explicitly set by the scraper or AI
      (not defaulted from source_url — that default was removed).
    - contact_email weight reduced to 0.03 (rarely published on gov sites).
    """
    weights = {
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
    score = 0.0
    for field, weight in weights.items():
        v = grant.get(field)
        if isinstance(v, list) and len(v) > 0:
            score += weight
        elif v:
            score += weight
    # deadline and rolling share a single 0.15 slot
    if grant.get("deadline") or grant.get("rolling"):
        score += 0.15
    return round(score, 2)


def clean_and_validate(raw: Dict, state: str, source_url: str) -> Dict:
    """
    Take raw extracted data (from regex + AI), clean it, add metadata,
    and return a dict ready for the database.
    """
    grant = raw.copy()

    grant.setdefault("title",                    "Untitled Grant")
    grant.setdefault("description",              None)
    grant.setdefault("summary",                  None)
    grant.setdefault("deadline",                 None)
    grant.setdefault("rolling",                  False)
    grant.setdefault("is_annual",                False)
    grant.setdefault("posted_date",              None)
    grant.setdefault("award_min",                None)
    grant.setdefault("award_max",                None)
    grant.setdefault("total_funding",            None)
    grant.setdefault("eligibility_individual",   False)
    grant.setdefault("eligibility_organization", True)
    grant.setdefault("eligibility_notes",        None)
    grant.setdefault("contact_email",            None)
    grant.setdefault("contact_name",             None)
    grant.setdefault("contact_phone",            None)
    grant.setdefault("tags",                     [])
    grant.setdefault("areas_of_focus",           [])
    grant.setdefault("sdg_alignment",            [])
    grant.setdefault("industry",                 None)
    grant.setdefault("opportunity_url",          source_url)
    # application_url intentionally NOT defaulted to source_url.
    # Leaving it null lets the enrichment step fill it with a real apply link.
    # sync_opportunities.py falls back to opportunity_url when still null.
    grant.setdefault("fee_required",             False)
    grant.setdefault("equity_percentage",        False)
    grant.setdefault("safe_note",                False)
    grant.setdefault("logo_url",                 None)
    grant.setdefault("sponsor_website",          None)
    grant.setdefault("key_requirements",         [])

    grant["state"]            = state
    grant["opportunity_type"] = "grant"
    grant["extracted_at"]     = datetime.utcnow().isoformat()

    if grant["title"]:
        grant["title"] = grant["title"].strip()[:500]
    if grant["description"]:
        grant["description"] = grant["description"].strip()[:5000]
    if grant["summary"]:
        grant["summary"] = grant["summary"].strip()[:1000]

    for list_field in ["tags", "areas_of_focus", "sdg_alignment",
                       "eligible_applicant_types", "key_requirements"]:
        v = grant.get(list_field)
        if not isinstance(v, list):
            grant[list_field] = [v] if v else []

    grant["data_quality_score"] = calculate_quality_score(grant)
    grant["needs_review"] = (
        grant["data_quality_score"] < 0.5
        or not grant["deadline"]
        or grant.get("needs_review", False)
    )

    if grant["rolling"]:
        grant["status"] = "rolling"
    elif grant["deadline"]:
        try:
            dl = datetime.strptime(grant["deadline"], "%m/%d/%Y")
            days = (dl.date() - TODAY.date()).days
            if days < 0:
                grant["status"] = "recently_closed" if days > -30 else "archived"
            elif days <= 7:
                grant["status"] = "expiring_soon"
            else:
                grant["status"] = "active"
        except Exception:
            grant["status"] = "unverified"
    else:
        grant["status"] = "unverified"

    return grant


def load_to_db(grants: List[Dict], db_session) -> Dict:
    """
    Load a list of cleaned grants into the database.
    Skips duplicates based on application_url.
    Returns stats.
    """
    # Import here to avoid circular imports — add app/ to path
    from pathlib import Path as _Path
    sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "app"))
    try:
        from models import Opportunity, Source, State as StateModel
    except ImportError as e:
        log.error(f"Could not import models: {e}")
        return {"saved": 0, "skipped": 0, "errors": len(grants)}

    saved   = 0
    skipped = 0
    errors  = 0

    for g in grants:
        try:
            app_url = g.get("application_url") or g.get("opportunity_url")
            if not app_url:
                log.warning(f"  No URL for grant: {g.get('title')} — skipping")
                skipped += 1
                continue

            exists = db_session.query(Opportunity).filter(
                Opportunity.application_url == app_url
            ).first()
            if exists:
                log.info(f"  Already in DB: {g['title'][:60]}")
                skipped += 1
                continue

            state_obj = db_session.query(StateModel).filter(
                StateModel.code == g["state"]
            ).first()

            base_url = "/".join(app_url.split("/")[:3])
            source   = db_session.query(Source).filter(
                Source.url == base_url
            ).first()
            if not source:
                source = Source(
                    name=base_url.replace("https://", "").replace("http://", ""),
                    url=base_url,
                    scraper_type="web",
                    scrape_frequency_hours=24,
                    is_active=True,
                )
                db_session.add(source)
                db_session.flush()

            deadline    = None
            posted_date = None
            if g.get("deadline"):
                try:
                    deadline = datetime.strptime(g["deadline"], "%m/%d/%Y")
                except Exception:
                    pass
            if g.get("posted_date"):
                try:
                    posted_date = datetime.strptime(g["posted_date"], "%m/%d/%Y")
                except Exception:
                    pass

            status = g.get("status", "unverified")

            opp = Opportunity(
                title                    = g["title"][:500],
                description              = g.get("description"),
                summary                  = g.get("summary"),
                opportunity_type         = "grant",
                status                   = status,
                state_id                 = state_obj.id if state_obj else None,
                source_id                = source.id,
                deadline                 = deadline,
                posted_date              = posted_date,
                award_min                = g.get("award_min"),
                award_max                = g.get("award_max"),
                total_funding            = g.get("total_funding"),
                eligibility_individual   = g.get("eligibility_individual", False),
                eligibility_organization = g.get("eligibility_organization", True),
                eligibility_description  = g.get("eligibility_notes"),
                application_url          = app_url[:1000],
                contact_name             = g.get("contact_name"),
                contact_email            = g.get("contact_email"),
                contact_phone            = g.get("contact_phone"),
                data_quality_score       = g.get("data_quality_score", 0.0),
                needs_review             = g.get("needs_review", True),
                raw_source_data          = {"extraction": g},
                created_at               = datetime.utcnow(),
                updated_at               = datetime.utcnow(),
            )
            db_session.add(opp)
            saved += 1
            log.info(f"  Saved: {g['title'][:70]}")

        except Exception as e:
            log.error(f"  DB error for '{g.get('title', '?')}': {e}")
            errors += 1

    try:
        db_session.commit()
        log.info(f"Committed {saved} grants to DB")
    except Exception as e:
        db_session.rollback()
        log.error(f"Commit failed: {e}")
        return {"saved": 0, "skipped": skipped, "errors": errors + saved}

    return {"saved": saved, "skipped": skipped, "errors": errors}