"""
Pennsylvania Grants Search Scraper
Source: https://www.pa.gov/grants/search  (92 "Accepting Applications" grants)

Strategy:
  1. Fetch search page → discover Coveo API credentials (embedded in HTML/JS)
  2. Call Coveo REST API → get all 92 grants with FULL structured metadata
     (deadline timestamps, award amounts, descriptions, eligibility, agency, category)
  3. For each grant detail page → scrape for PDFs, contact info, extra body text
  4. Merge Coveo metadata (authoritative) + HTML page text + PDFs
  5. Azure OpenAI enrichment → summary, tags, areas_of_focus, missing fields
  6. clean_and_validate() → save data/pa/pa_grants_search_raw.json

Coveo raw fields used:
  copapwpclosedate     Unix ms timestamp → deadline
  copapwpopendate      Unix ms timestamp → posted_date
  copapwpoverview      Full description text
  copapwpshortdescription  Short summary
  copapwpapplicanttype / copapwpapplicantcategory → eligibility
  copapwpfundingagency → agency / sponsor
  copapwpcategory      → tags
  copapwpgrantcycle    → is_annual / rolling
  copapwpmaximumaward / copapwpminimumaward → award amounts
  copapwpfundingdetails → matching info
  copapwpbadges / copapwpapplicationstatus → status
"""

import os
import re
import sys
import json
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

THIS_DIR     = Path(__file__).resolve().parent
PROJECT_ROOT = THIS_DIR.parent.parent
BASE_DIR     = PROJECT_ROOT / "scrapers" / "base"
APP_DIR      = PROJECT_ROOT / "app"
DATA_DIR     = PROJECT_ROOT / "data" / "pa"

sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(APP_DIR))

from base_scraper import (
    safe_get, extract_pdf_text, extract_date, extract_amount,
    ai_extract, clean_and_validate, log,
)

STATE       = "PA"
BASE_URL    = "https://www.pa.gov"
SEARCH_URL  = "https://www.pa.gov/grants/search"
OUTPUT_FILE = str(DATA_DIR / "pa_grants_search_raw.json")

COVEO_REST  = "https://platform.cloud.coveo.com/rest/search/v2"
COVEO_FILTER = '@copapwpapplicationstatus=="Accepting applications"'

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ts_to_date(ts) -> Optional[str]:
    """
    Convert a Coveo Unix millisecond timestamp to MM/DD/YYYY.
    pa.gov Coveo stores dates as 13-digit ms-since-epoch integers.
    Only accepts years 2025–2030.
    """
    if not ts:
        return None
    try:
        dt = datetime.fromtimestamp(int(ts) / 1000)
        if 2025 <= dt.year <= 2030:
            return dt.strftime("%m/%d/%Y")
    except Exception:
        pass
    return None


def _coerce_list(v) -> List[str]:
    """Ensure a value is a list of strings."""
    if isinstance(v, list):
        return [str(x) for x in v if x]
    if v:
        return [str(v)]
    return []


def _parse_award(v) -> Optional[float]:
    """Convert Coveo award value (int/float/str) to float. 0 means 'not specified'."""
    try:
        f = float(v)
        return f if f > 0 else None
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 1 — Coveo API discovery + result fetch
# ─────────────────────────────────────────────────────────────────────────────

def _discover_coveo_config(html: str) -> Dict:
    """Extract Coveo org ID and access token from page HTML."""
    config: Dict = {}
    for pat in [
        r'"organizationId"\s*:\s*"([^"]+)"',
        r"organizationId\s*[=:]\s*['\"]([^'\"]+)['\"]",
    ]:
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            config["org_id"] = m.group(1)
            break
    for pat in [
        r'"accessToken"\s*:\s*"([^"]+)"',
        r'"searchToken"\s*:\s*"([^"]+)"',
        r"accessToken\s*[=:]\s*['\"]([^'\"]+)['\"]",
    ]:
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            config["token"] = m.group(1)
            break
    return config


def _call_coveo_api(config: Dict) -> List[Dict]:
    """Call Coveo REST API and return all accepting-application grants."""
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if config.get("token"):
        headers["Authorization"] = f"Bearer {config['token']}"
    params = {"organizationId": config["org_id"]} if config.get("org_id") else {}

    all_results: List[Dict] = []
    first = 0
    batch = 100

    while True:
        payload = {
            "q":              "",
            "aq":             COVEO_FILTER,
            "numberOfResults": batch,
            "firstResult":    first,
            "sortCriteria":   "@copapwpclosedate ascending",
        }
        try:
            r = requests.post(COVEO_REST, params=params, headers=headers,
                              json=payload, timeout=30)
            if r.status_code != 200:
                log.warning(f"  Coveo API HTTP {r.status_code}")
                break
            data    = r.json()
            results = data.get("results", [])
            if not results:
                break
            all_results.extend(results)
            total = data.get("totalCount", 0)
            log.info(f"  Coveo batch firstResult={first}: got {len(results)}, "
                     f"total available={total}")
            first += len(results)
            if first >= total:
                break
        except Exception as e:
            log.warning(f"  Coveo API error: {e}")
            break

    return all_results


def _build_grant_info_from_coveo(r: Dict) -> Dict:
    """
    Extract all useful metadata from a single Coveo result object.
    This is the PRIMARY data source — the detail page scrape supplements it.
    """
    raw = r.get("raw", {})

    # ── Dates ─────────────────────────────────────────────────────────────
    deadline   = _ts_to_date(raw.get("copapwpclosedate"))
    open_date  = _ts_to_date(raw.get("copapwpopendate"))

    # ── Grant frequency / rolling / annual ────────────────────────────────
    cycle = str(raw.get("copapwpgrantcycle", "") or "").lower()
    is_annual = "annual" in cycle or "year" in cycle
    rolling   = is_annual or "rolling" in cycle or "ongoing" in cycle

    # ── Award amounts ─────────────────────────────────────────────────────
    award_max = _parse_award(raw.get("copapwpmaximumaward"))
    award_min = _parse_award(raw.get("copapwpminimumaward"))

    # ── Funding details (matching, etc.) ──────────────────────────────────
    funding_details = str(raw.get("copapwpfundingdetails") or "")
    matching_required = "match" in funding_details.lower() and "no match" not in funding_details.lower()

    # ── Eligibility ───────────────────────────────────────────────────────
    applicant_types    = _coerce_list(raw.get("copapwpapplicanttype"))
    applicant_category = str(raw.get("copapwpapplicantcategory") or "")
    eligibility_parts  = []
    if applicant_types:
        eligibility_parts.append("Eligible applicants: " + "; ".join(applicant_types))
    if applicant_category and applicant_category not in ("None", ""):
        eligibility_parts.append(f"Category: {applicant_category}")
    if funding_details:
        eligibility_parts.append(f"Funding details: {funding_details}")
    eligibility_notes = "\n".join(eligibility_parts) if eligibility_parts else None

    # ── Agency ────────────────────────────────────────────────────────────
    agency_list = _coerce_list(raw.get("copapwpfundingagency"))
    agency      = agency_list[0] if agency_list else ""

    # ── Categories / tags ─────────────────────────────────────────────────
    category_list = _coerce_list(raw.get("copapwpcategory"))

    # ── Description ───────────────────────────────────────────────────────
    overview     = str(raw.get("copapwpoverview")          or "").strip()
    short_desc   = str(raw.get("copapwpshortdescription")  or "").strip()
    description  = overview or short_desc or ""

    # ── URL ───────────────────────────────────────────────────────────────
    # Coveo returns URLs with /en/ prefix: https://www.pa.gov/en/grants/search/grant-details/{agency}/{id}
    url = r.get("clickUri") or r.get("uri") or ""
    if not url.startswith("http"):
        url = urljoin(BASE_URL, url)

    return {
        "url":               url,
        "title":             raw.get("copapwppagetitle") or r.get("title", ""),
        "agency":            agency,
        "deadline":          deadline,
        "open_date":         open_date,
        "is_annual":         is_annual,
        "rolling":           rolling,
        "grant_cycle":       raw.get("copapwpgrantcycle", ""),
        "award_max":         award_max,
        "award_min":         award_min,
        "matching_required": matching_required,
        "description":       description,
        "eligibility_notes": eligibility_notes,
        "applicant_types":   applicant_types,
        "category":          category_list,
        "raw_coveo":         raw,
    }


def discover_grants() -> List[Dict]:
    """Layer 1: Returns enriched grant info dicts from Coveo API."""
    log.info(f"LAYER 1 — Discovering grants from {SEARCH_URL}")
    r = safe_get(SEARCH_URL)
    if not r:
        log.error("Cannot reach search page")
        return []

    config = _discover_coveo_config(r.text)
    if config:
        log.info(f"  Coveo config: org={config.get('org_id')} "
                 f"token={'yes' if config.get('token') else 'no'}")
        coveo_results = _call_coveo_api(config)
        if coveo_results:
            grants = [_build_grant_info_from_coveo(res) for res in coveo_results]
            # Remove duplicates by URL
            seen = set()
            unique = []
            for g in grants:
                if g["url"] not in seen:
                    seen.add(g["url"])
                    unique.append(g)
            log.info(f"LAYER 1 complete — {len(unique)} grants via Coveo API")
            return unique

    log.warning("Coveo API unavailable — falling back to HTML link scan")
    soup = BeautifulSoup(r.text, "html.parser")
    seen, grants = set(), []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if "grant-details" in href:
            if not href.startswith("http"):
                href = urljoin(BASE_URL, href)
            if href not in seen:
                seen.add(href)
                grants.append({"url": href, "title": a.get_text(strip=True),
                               "agency": "", "description": "", "eligibility_notes": None,
                               "deadline": None, "award_max": None, "category": []})
    log.info(f"LAYER 1 complete (HTML fallback) — {len(grants)} grants")
    return grants


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 2 — Detail page scraping (PDFs + contact + application URL)
# ─────────────────────────────────────────────────────────────────────────────

def scrape_detail_page(grant_info: Dict) -> Optional[Dict]:
    """
    Fetch the grant detail page and extract:
    - Application URL (actual form link, if resolvable from static HTML)
    - Contact information
    - PDF attachment links
    - Any structured data not covered by Coveo (backup dates/amounts)
    - Full page text for AI enrichment context

    NOTE: The Key Dates and Funding cards are React-rendered and won't be visible
    to BeautifulSoup. All structured metadata comes from Coveo (Layer 1).
    """
    url = grant_info["url"]
    r   = safe_get(url)
    if not r:
        return None

    soup      = BeautifulSoup(r.content, "html.parser")
    page_text = soup.get_text(separator="\n", strip=True)

    result: Dict = {
        "source_url":      url,
        "page_text":       page_text,
        "application_url": None,
        "contact_text":    None,
        "pdf_links":       [],
        "resource_links":  [],
    }

    # ── Application URL ────────────────────────────────────────────────────
    # The "Go to Application" button uses a JS template on some pages:
    # href="${linkToApply}" — we must avoid capturing those.
    for a in soup.find_all("a", href=True):
        label = a.get_text(strip=True).lower()
        href  = a["href"].strip()

        # Skip JS template placeholders
        if "${" in href or href.startswith("#"):
            continue

        if any(kw in label for kw in [
            "go to application", "apply now", "apply here", "application portal",
            "submit application", "start application", "apply online", "apply",
        ]):
            if href.startswith("http") and href != url:
                result["application_url"] = href
                break
            elif href and not href.startswith("#"):
                candidate = urljoin(BASE_URL, href)
                if candidate != url:
                    result["application_url"] = candidate
                    break

    # ── Contact information ─────────────────────────────────────────────────
    for heading in soup.find_all(["h2", "h3", "h4"]):
        if "contact" in heading.get_text(strip=True).lower():
            parent = heading.find_parent()
            if parent:
                result["contact_text"] = parent.get_text(separator="\n", strip=True)[:600]
            break

    # Extract email addresses from page text
    emails = re.findall(r"[\w.+-]+@[\w.-]+\.\w{2,}", page_text)
    if emails:
        result["contact_email"] = emails[0]

    # ── PDFs and resource links ─────────────────────────────────────────────
    seen_pdfs  = set()
    seen_links = set()
    for a in soup.find_all("a", href=True):
        href  = a["href"].strip()
        label = a.get_text(strip=True)
        if not href or "${" in href or href.startswith("#"):
            continue
        if not href.startswith("http"):
            href = urljoin(BASE_URL, href)

        if href.lower().endswith(".pdf") and href not in seen_pdfs:
            seen_pdfs.add(href)
            result["pdf_links"].append({"url": href, "label": label})
        elif (href not in seen_links and href != url
              and "pa.gov" in href and len(label) > 3):
            seen_links.add(href)
            result["resource_links"].append({"url": href, "label": label})

    return result


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 3 — PDF extraction
# ─────────────────────────────────────────────────────────────────────────────

def process_pdfs(page: Dict) -> Dict:
    """Download and extract text from PDF attachments (max 3 per grant)."""
    pdf_extractions = []
    for pdf in page.get("pdf_links", [])[:3]:
        log.info(f"    PDF: {pdf['label'][:60]}")
        text = extract_pdf_text(pdf["url"])
        if not text:
            continue
        pdf_extractions.append({
            "url":    pdf["url"],
            "label":  pdf["label"],
            "text":   text,
            "date":   extract_date(text),
            "amount": extract_amount(text),
        })
    page["pdf_extractions"] = pdf_extractions
    return page


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 4 — Merge + Azure AI enrichment
# ─────────────────────────────────────────────────────────────────────────────

def merge_and_enrich(grant_info: Dict, page: Dict) -> Dict:
    """
    Primary source: Coveo metadata (grant_info) — has authoritative dates/amounts.
    Secondary: detail page HTML + PDFs — used for contact, extra body text, AI.

    Azure OpenAI fills: summary, tags, areas_of_focus, sector, SDG alignment,
    refined eligibility, and any gaps not covered by Coveo.
    """
    # ── Build combined text corpus ─────────────────────────────────────────
    # Start with Coveo description (most reliable)
    parts = []
    if grant_info.get("description"):
        parts.append(f"GRANT DESCRIPTION:\n{grant_info['description']}")
    if grant_info.get("eligibility_notes"):
        parts.append(f"ELIGIBILITY:\n{grant_info['eligibility_notes']}")
    if grant_info.get("agency"):
        parts.append(f"FUNDING AGENCY: {grant_info['agency']}")
    if grant_info.get("category"):
        parts.append(f"CATEGORIES: {', '.join(grant_info['category'])}")
    if grant_info.get("grant_cycle"):
        parts.append(f"GRANT CYCLE: {grant_info['grant_cycle']}")

    # Add page body text (strips navigation/boilerplate automatically via AI)
    page_text = page.get("page_text", "")
    if page_text:
        parts.append(f"DETAIL PAGE TEXT:\n{page_text[:6000]}")

    # Add PDF text
    for pdf in page.get("pdf_extractions", []):
        if pdf.get("text"):
            parts.append(f"ATTACHMENT ({pdf['label']}):\n{pdf['text'][:2000]}")

    combined_text = "\n\n".join(parts)

    # ── Determine best deadline ────────────────────────────────────────────
    deadline   = grant_info.get("deadline")
    rolling    = grant_info.get("rolling", False)
    is_annual  = grant_info.get("is_annual", False)

    # Check PDFs for better deadline if Coveo has none
    if not deadline:
        for pdf in page.get("pdf_extractions", []):
            d = pdf.get("date", {})
            if d.get("deadline"):
                deadline = d["deadline"]
                break
        if not deadline:
            d = extract_date(page_text)
            deadline  = d.get("deadline")
            rolling   = rolling or d.get("rolling", False)
            is_annual = is_annual or d.get("is_annual", False)

    # ── Determine best application URL ────────────────────────────────────
    app_url = page.get("application_url")
    # If still None or JS template, fall back to the grant detail page URL
    if not app_url or "${" in str(app_url):
        app_url = grant_info["url"]

    # ── Build the pre-merged record ───────────────────────────────────────
    merged: Dict = {
        "title":            grant_info.get("title", ""),
        "source_url":       grant_info["url"],
        "opportunity_url":  grant_info["url"],
        "application_url":  app_url,
        "agency":           grant_info.get("agency", ""),
        "description":      grant_info.get("description", ""),
        "deadline":         deadline,
        "rolling":          rolling,
        "is_annual":        is_annual,
        "award_min":        grant_info.get("award_min"),
        "award_max":        grant_info.get("award_max"),
        "eligibility_notes": grant_info.get("eligibility_notes"),
        "contact_email":    page.get("contact_email"),
        "tags":             grant_info.get("category", []),
        "combined_text":    combined_text,
        "needs_review":     True,
    }

    # ── Azure AI enrichment ───────────────────────────────────────────────
    ai = ai_extract(combined_text, grant_info["url"], STATE)

    if isinstance(ai, dict):
        final = {**ai}

        # Coveo-derived fields take priority over AI guesses
        for key in ("deadline", "award_max", "award_min", "is_annual"):
            if merged.get(key) is not None and not final.get(key):
                final[key] = merged[key]

        if merged["rolling"] and not final.get("rolling"):
            final["rolling"] = True
        if merged["is_annual"]:
            final["is_annual"] = True
            final["rolling"]   = True

        # Preserve our rich eligibility if AI left it empty
        if merged.get("eligibility_notes") and not final.get("eligibility_notes"):
            final["eligibility_notes"] = merged["eligibility_notes"]

        # Keep Coveo description if AI summary is thin
        if merged.get("description") and not final.get("description"):
            final["description"] = merged["description"]

        # Merge tags (union of Coveo categories + AI tags)
        ai_tags    = final.get("tags") or []
        coveo_tags = merged.get("tags") or []
        if isinstance(ai_tags, list) and isinstance(coveo_tags, list):
            final["tags"] = list(dict.fromkeys(coveo_tags + ai_tags))

        # Fix URLs
        final["opportunity_url"] = grant_info["url"]
        if not final.get("application_url") or "${" in str(final.get("application_url", "")):
            final["application_url"] = merged["application_url"]

        # Contact email from page
        if page.get("contact_email") and not final.get("contact_email"):
            final["contact_email"] = page["contact_email"]

        return final

    # AI unavailable — return merged dict with what we have
    return merged


# ─────────────────────────────────────────────────────────────────────────────
# Main orchestration
# ─────────────────────────────────────────────────────────────────────────────

def run(save_json: bool = True) -> List[Dict]:
    """Entry point: orchestrates all 4 layers, returns list of cleaned grant dicts."""
    log.info("=" * 70)
    log.info(f"PA GRANTS SEARCH SCRAPER — {SEARCH_URL}")
    log.info("=" * 70)
    start = datetime.now()

    # Layer 1 — Discover all grants + extract rich Coveo metadata
    grant_infos = discover_grants()
    if not grant_infos:
        log.error("No grants discovered — aborting.")
        return []

    grants: List[Dict] = []

    for i, grant_info in enumerate(grant_infos, 1):
        log.info(f"\n{'─' * 60}")
        log.info(f"{i}/{len(grant_infos)}: {grant_info.get('title', grant_info['url'])[:70]}")
        log.info(f"  Coveo: deadline={grant_info.get('deadline')}  "
                 f"award_max={grant_info.get('award_max')}  "
                 f"agency={grant_info.get('agency', '')[:40]}")
        try:
            # Layer 2 — scrape detail page (PDFs, contact, app URL)
            page = scrape_detail_page(grant_info)
            if not page:
                log.warning("  Could not fetch detail page — using Coveo data only")
                page = {"page_text": "", "pdf_links": [], "pdf_extractions": [],
                        "application_url": None, "contact_email": None}

            # Layer 3 — extract PDFs
            page = process_pdfs(page)

            # Layer 4 — merge + AI enrichment
            final   = merge_and_enrich(grant_info, page)
            cleaned = clean_and_validate(final, STATE, grant_info["url"])

            log.info(
                f"  FINAL: deadline={cleaned.get('deadline')}  "
                f"award_max={cleaned.get('award_max')}  "
                f"score={cleaned.get('data_quality_score')}  "
                f"status={cleaned.get('status')}"
            )
            grants.append(cleaned)

        except Exception as e:
            log.error(f"  Error for {grant_info.get('url', '?')}: {e}", exc_info=False)

        time.sleep(0.6)   # polite crawl delay

    # Save JSON
    if save_json:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        output = {
            "scraped_at": datetime.utcnow().isoformat(),
            "source":     SEARCH_URL,
            "state":      STATE,
            "total":      len(grants),
            "grants":     grants,
        }
        with open(OUTPUT_FILE, "w") as f:
            json.dump(output, f, indent=2, default=str)
        log.info(f"\nSaved {len(grants)} grants → {OUTPUT_FILE}")

    duration = (datetime.now() - start).total_seconds()
    active   = [g for g in grants if g.get("status") == "active"]
    rolling  = [g for g in grants if g.get("rolling")]
    review   = [g for g in grants if g.get("needs_review")]
    log.info(
        f"\nDone in {duration:.1f}s — {len(grants)} grants  "
        f"({len(active)} active | {len(rolling)} rolling | {len(review)} need review)"
    )
    return grants


if __name__ == "__main__":
    run(save_json=True)
