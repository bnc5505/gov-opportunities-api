"""
NY Grants Gateway Scraper
Source: https://grantsgateway.ny.gov

Scrapes the NY State Grants Gateway (IntelliGrants) public Browse portal.
The Browse endpoint returns a static HTML table of open opportunities —
no JavaScript execution or login is required to read it.

Each row: Funding Agency | Grant Opportunity | Status | Eligibility |
          Availability Date | Anticipated Release Date | Due Date

Then POSTs back to the portal to load the detail view for each grant
(description, contact, full eligibility text). Falls back to table data
if the detail POST fails.

Output: data/ny/ny_grants_gateway_raw.json
"""

import os
import sys
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional
from bs4 import BeautifulSoup

THIS_DIR     = Path(__file__).resolve().parent
PROJECT_ROOT = THIS_DIR.parent.parent
BASE_DIR     = PROJECT_ROOT / "scrapers" / "base"
APP_DIR      = PROJECT_ROOT / "app"
DATA_DIR     = PROJECT_ROOT / "data" / "ny"

sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(APP_DIR))

from base_scraper import (
    safe_get, extract_date, extract_amount,
    ai_extract, clean_and_validate, log, _cache,
)

STATE        = "NY"
PORTAL_BASE  = "https://grantsgateway.ny.gov/IntelliGrants_NYSGG/module/nysgg"
BROWSE_URL   = f"{PORTAL_BASE}/goportal.aspx?NavItem1=2"
SOURCE_URL   = "https://grantsgateway.ny.gov/"
OUTPUT_FILE  = str(DATA_DIR / "ny_grants_gateway_raw.json")

import requests as _requests

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def _session() -> _requests.Session:
    s = _requests.Session()
    s.headers.update(_HEADERS)
    return s


def _viewstate(soup: BeautifulSoup) -> dict:
    """Extract ASP.NET hidden form fields for subsequent POSTs."""
    fields = {}
    for name in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION",
                 "__LASTFOCUS", "__EVENTTARGET", "__EVENTARGUMENT"):
        inp = soup.find("input", {"name": name})
        if inp:
            fields[name] = inp.get("value", "")
    return fields


def _find_grants_table(soup: BeautifulSoup) -> Optional[object]:
    """Return the <table> whose header row contains 'Funding Agency'."""
    for table in soup.find_all("table"):
        header = table.find("tr")
        if header and "Funding Agency" in header.get_text():
            return table
    return None


def scrape_browse() -> List[Dict]:
    """Layer 1 — fetch the Browse page and parse the grants table."""
    log.info(f"LAYER 1 — Browse: {BROWSE_URL}")
    sess = _session()

    try:
        r = sess.get(BROWSE_URL, timeout=10)
        r.raise_for_status()
    except Exception as e:
        log.error(f"Could not fetch browse page: {e}")
        return []

    soup   = BeautifulSoup(r.content, "html.parser")
    table  = _find_grants_table(soup)
    if not table:
        log.warning("No grants table found on browse page")
        return []

    rows = table.find_all("tr")[1:]   # skip header
    grants_raw = []
    for row in rows:
        cells = row.find_all(["td", "th"])
        if len(cells) < 5:
            continue
        agency      = cells[0].get_text(strip=True)
        title       = cells[1].get_text(strip=True)
        status      = cells[2].get_text(strip=True).lower()
        eligibility = cells[3].get_text(strip=True)
        avail_date  = cells[4].get_text(strip=True)
        release_dt  = cells[5].get_text(strip=True) if len(cells) > 5 else ""
        due_date    = cells[6].get_text(strip=True) if len(cells) > 6 else ""

        if not title or not agency:
            continue

        # Extract PostBack target from Apply button for detail fetch
        postback_target = None
        for a in row.find_all("a", href=True):
            href = a["href"]
            if "btnApply" in href:
                import re
                m = re.search(r'"(ctl00[^"]+btnApply)"', href)
                if m:
                    postback_target = m.group(1)

        grants_raw.append({
            "agency":           agency,
            "title":            title,
            "status":           status,
            "eligibility":      eligibility,
            "avail_date":       avail_date,
            "release_date":     release_dt,
            "due_date":         due_date,
            "postback_target":  postback_target,
            "_soup":            soup,
            "_session":         sess,
            "_r":               r,
        })
        log.info(f"  Found: {title[:70]} ({agency})")

    log.info(f"LAYER 1 complete — {len(grants_raw)} grants in browse table")
    return grants_raw


def fetch_detail(grant_raw: Dict) -> Optional[str]:
    """
    Layer 2 — POST to the portal to load the detail view for this grant.
    Returns the page text if successful, None otherwise.
    """
    postback = grant_raw.get("postback_target")
    if not postback:
        return None

    sess = grant_raw["_session"]
    soup = grant_raw["_soup"]
    r0   = grant_raw["_r"]

    fields = _viewstate(soup)
    fields["__EVENTTARGET"]   = postback
    fields["__EVENTARGUMENT"] = ""
    fields["ctl00$VIntelliGrants"]  = ""
    fields["ctl00$hdnCurrentTheme"] = "NYSGG"
    fields["ctl00$cphPageContent$hdnGrantOpportunityID"] = "0"
    fields["ctl00$cphPageContent$hdnNRGID"] = "0"

    try:
        r2 = sess.post(
            f"{PORTAL_BASE}/goportal.aspx",
            data=fields,
            timeout=10,
            headers={"Referer": BROWSE_URL},
        )
        r2.raise_for_status()
        soup2 = BeautifulSoup(r2.content, "html.parser")
        return soup2.get_text(separator="\n", strip=True)
    except Exception as e:
        log.warning(f"  Detail fetch failed for '{grant_raw['title'][:50]}': {e}")
        return None


def build_grant(grant_raw: Dict) -> Dict:
    """Build the combined_text block and call AI enrichment."""
    title       = grant_raw["title"]
    agency      = grant_raw["agency"]
    eligibility = grant_raw["eligibility"]
    status_str  = grant_raw["status"]
    avail_date  = grant_raw["avail_date"]
    due_date    = grant_raw["due_date"]

    # Fetch detail page for richer description text
    detail_text = fetch_detail(grant_raw)

    # Build combined_text from what we have
    combined_parts = [
        f"Grant: {title}",
        f"Agency: {agency}",
        f"Status: {status_str}",
        f"Eligibility: {eligibility}",
    ]
    if avail_date and avail_date.upper() != "NA":
        combined_parts.append(f"Available since: {avail_date}")
    if due_date and due_date.upper() not in ("NA", ""):
        combined_parts.append(f"Due Date: {due_date}")
    if detail_text:
        combined_parts.append(detail_text[:4000])

    combined_text = "\n".join(combined_parts)

    # Deadline: only set if a real date is given
    deadline_parsed = extract_date(combined_text)
    is_rolling = (not due_date or due_date.upper() in ("NA", "")) or bool(deadline_parsed.get("rolling"))

    # AI enrichment
    ai = ai_extract(combined_text, SOURCE_URL, STATE) or {}
    final = {**ai}
    final["combined_text"] = combined_text

    if not final.get("title"):
        final["title"] = title
    if not final.get("deadline") and not is_rolling:
        dl = deadline_parsed.get("deadline")
        if dl:
            final["deadline"] = dl
    if not final.get("rolling") and is_rolling:
        final["rolling"] = True
    if not final.get("description"):
        final["description"] = (
            f"{agency} — {title}. Eligibility: {eligibility}. "
            f"Status: {status_str}."
        )

    final["opportunity_url"]  = SOURCE_URL
    final["application_url"]  = SOURCE_URL

    return final


def run(save_json: bool = True) -> List[Dict]:
    log.info("=" * 70)
    log.info(f"NY GRANTS GATEWAY SCRAPER — {BROWSE_URL}")
    log.info("=" * 70)
    start = datetime.now()

    grants_raw = scrape_browse()
    grants     = []

    for i, raw in enumerate(grants_raw, 1):
        try:
            final   = build_grant(raw)
            # Remove internal scraping state before passing to validate
            for k in ("_soup", "_session", "_r", "postback_target",
                      "agency", "avail_date", "release_date", "due_date", "eligibility"):
                raw.pop(k, None)
            cleaned = clean_and_validate(final, STATE, SOURCE_URL)
            log.info(
                f"  [{i}/{len(grants_raw)}] {cleaned.get('title','?')[:60]}  "
                f"rolling={cleaned.get('rolling')}  "
                f"score={cleaned.get('data_quality_score')}"
            )
            grants.append(cleaned)
        except Exception as e:
            log.error(f"  Error on grant {i}: {e}")

    _cache.flush()

    if save_json:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        out = {
            "scraped_at": datetime.utcnow().isoformat(),
            "source":     SOURCE_URL,
            "state":      STATE,
            "total":      len(grants),
            "grants":     grants,
        }
        with open(OUTPUT_FILE, "w") as f:
            json.dump(out, f, indent=2, default=str)
        log.info(f"\nSaved {len(grants)} grants → {OUTPUT_FILE}")

    duration = (datetime.now() - start).total_seconds()
    log.info(f"Done in {duration:.1f}s — {len(grants)} grants")
    return grants


if __name__ == "__main__":
    run(save_json=True)
