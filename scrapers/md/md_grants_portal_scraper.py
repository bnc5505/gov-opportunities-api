"""
Maryland Governor's Grants Office — State Grants Portal Scraper
Source: https://grants.maryland.gov/Pages/StateGrants.aspx

Scrapes the Governor's Grants Office central listing of Maryland state grant
programs. The page renders a full HTML table even without JavaScript — the
JS-required warning appears in the page but doesn't prevent data access.

Table columns: Organization | Grant Title | Due Date
Each title cell links to the relevant agency page.

The scraper visits each detail page to extract description, eligibility, and
deadline text, then runs Azure AI enrichment.

Output: data/md/md_grants_portal_raw.json
"""

import os
import sys
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

THIS_DIR     = Path(__file__).resolve().parent
PROJECT_ROOT = THIS_DIR.parent.parent
BASE_DIR     = PROJECT_ROOT / "scrapers" / "base"
APP_DIR      = PROJECT_ROOT / "app"
DATA_DIR     = PROJECT_ROOT / "data" / "md"

sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(APP_DIR))

from base_scraper import (
    safe_get, extract_pdf_text, extract_date, extract_amount,
    ai_extract, clean_and_validate, log, _cache,
)

STATE        = "MD"
LISTING_URL  = "https://grants.maryland.gov/Pages/StateGrants.aspx"
BASE_URL     = "https://grants.maryland.gov"
OUTPUT_FILE  = str(DATA_DIR / "md_grants_portal_raw.json")

SKIP_DOMAINS = {
    "zoom.us",               # webinar registration links
    "govdelivery.com",       # email subscription links
    "forms.office.com",      # Office 365 forms
    "docs.google.com",       # Google forms
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def scrape_listing() -> List[Dict]:
    """Layer 1 — Parse the StateGrants table for all grant rows."""
    log.info(f"LAYER 1 — Listing: {LISTING_URL}")
    r = safe_get(LISTING_URL)
    if not r:
        log.error("Could not fetch listing page")
        return []

    soup  = BeautifulSoup(r.content, "html.parser")
    table = soup.find("table")
    if not table:
        log.warning("No table found on listing page")
        return []

    rows  = table.find_all("tr")
    items = []
    seen  = set()

    for row in rows[1:]:   # skip header row
        cells = row.find_all("td")
        if len(cells) < 2:
            continue

        org   = cells[0].get_text(strip=True)
        title_cell = cells[1]
        title = title_cell.get_text(strip=True)
        due   = cells[2].get_text(strip=True) if len(cells) > 2 else ""

        if not title or not org:
            continue

        # Extract link from title cell
        a_tag = title_cell.find("a", href=True)
        link  = a_tag["href"].strip() if a_tag else ""

        # Skip bad links
        if link.startswith("file:///") or not link:
            link = ""
        elif not link.startswith("http"):
            link = urljoin(BASE_URL, link)

        # Skip zoom/govdelivery/form-only links
        if link:
            domain = urlparse(link).netloc
            if domain in SKIP_DOMAINS:
                link = ""

        # Dedup by title
        key = title.lower().strip()
        if key in seen:
            continue
        seen.add(key)

        items.append({
            "org":   org,
            "title": title,
            "due":   due,
            "url":   link,
        })
        log.info(f"  Found: [{org[:35]}] {title[:60]}")

    log.info(f"LAYER 1 complete — {len(items)} grant entries")
    return items


def scrape_detail(item: Dict) -> Optional[Dict]:
    """Layer 2 — Visit the linked agency page to get description text."""
    url   = item["url"]
    title = item["title"]

    if not url:
        return {
            "url":       "",
            "name":      title,
            "page_text": "",
            "pdf_links": [],
            "html_date": {},
            "html_amount": {},
            "_content_hash": "",
        }

    log.info(f"LAYER 2 — {title[:60]}")
    r = safe_get(url)
    if not r:
        return None

    chash  = _cache.content_hash(r.content)
    cached = _cache.get_if_fresh(url, chash)
    if cached:
        log.info(f"    [cache hit] {title[:60]}")
        return {"_from_cache": True, "_cached_grant": cached}

    soup      = BeautifulSoup(r.content, "html.parser")
    page_text = soup.get_text(separator="\n", strip=True)

    pdf_links = []
    seen_pdfs = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().endswith(".pdf"):
            if not href.startswith("http"):
                href = urljoin(url, href)
            if href not in seen_pdfs:
                seen_pdfs.add(href)
                pdf_links.append({"url": href, "label": a.get_text(strip=True)})

    return {
        "url":            url,
        "name":           title,
        "page_text":      page_text,
        "pdf_links":      pdf_links,
        "html_date":      extract_date(page_text),
        "html_amount":    extract_amount(page_text),
        "_content_hash":  chash,
    }


def build_grant(item: Dict, page: Optional[Dict]) -> Dict:
    """Merge listing row + detail page data, run AI enrichment."""
    org   = item["org"]
    title = item["title"]
    due   = item["due"]
    url   = item["url"]

    # Combined text
    parts = [f"Organization: {org}", f"Grant: {title}"]
    if due:
        parts.append(f"Due Date: {due}")

    page_text = ""
    if page and not page.get("_from_cache"):
        page_text = page.get("page_text", "")
        if page_text:
            parts.append(page_text[:3000])

    combined_text = "\n".join(parts)

    # Best date signal
    conf_rank = {"high": 3, "medium": 2, "low": 1}
    deadline, is_rolling = None, False
    date_candidates = []

    if due:
        date_candidates.append(extract_date(due))
    if page and page.get("html_date"):
        date_candidates.append(page["html_date"])

    if date_candidates:
        best = max(date_candidates, key=lambda x: conf_rank.get(x.get("confidence", "low"), 0))
        deadline   = best.get("deadline")
        is_rolling = best.get("rolling", False)

    # Most grants on this portal have no due date — treat as rolling
    if not deadline and not due:
        is_rolling = True11

    # Best amount
    ha = page.get("html_amount", {}) if page else {}
    award_min = ha.get("award_min") if ha else None
    award_max = ha.get("award_max") if ha else None

    # AI enrichment
    ai = ai_extract(combined_text, url or LISTING_URL, STATE) or {}
    final = {**ai}
    final["combined_text"] = combined_text

    if not final.get("title"):
        final["title"] = f"{org} — {title}" if org else title
    if not final.get("deadline") and deadline:
        final["deadline"] = deadline
    if not final.get("rolling") and is_rolling:
        final["rolling"] = True
    if not final.get("award_min") and award_min:
        final["award_min"] = award_min
    if not final.get("award_max") and award_max:
        final["award_max"] = award_max
    if not final.get("description"):
        final["description"] = f"{org}: {title}."

    final["opportunity_url"] = url or LISTING_URL
    if not final.get("application_url"):
        final["application_url"] = url or LISTING_URL

    return final


def run(save_json: bool = True) -> List[Dict]:
    log.info("=" * 70)
    log.info(f"MD GOVERNOR'S GRANTS PORTAL SCRAPER — {LISTING_URL}")
    log.info("=" * 70)
    start = datetime.now()

    items  = scrape_listing()
    grants = []

    def _process_one(item):
        try:
            page = scrape_detail(item)
            if page is None:
                # Detail page failed — build from listing row only
                page = {
                    "url":       item["url"],
                    "name":      item["title"],
                    "page_text": "",
                    "pdf_links": [],
                    "html_date": {},
                    "html_amount": {},
                    "_content_hash": "",
                }
            if page.get("_from_cache"):
                return page["_cached_grant"]

            chash = page.pop("_content_hash", None)
            final   = build_grant(item, page)
            cleaned = clean_and_validate(final, STATE, item["url"] or LISTING_URL)
            if chash and item["url"]:
                _cache.set(item["url"], chash, cleaned)
            return cleaned
        except Exception as e:
            log.error(f"  Error on '{item.get('title','?')[:50]}': {e}")
            return None

    with ThreadPoolExecutor(max_workers=5) as exe:
        futures = {exe.submit(_process_one, it): it for it in items}
        for i, fut in enumerate(as_completed(futures), 1):
            it = futures[fut]
            try:
                cleaned = fut.result()
                if cleaned:
                    log.info(
                        f"  [{i}/{len(items)}] {it['title'][:50]}  "
                        f"deadline={cleaned.get('deadline')}  "
                        f"score={cleaned.get('data_quality_score')}"
                    )
                    grants.append(cleaned)
            except Exception as e:
                log.error(f"  Future error on '{it.get('title','?')[:40]}': {e}")

    _cache.flush()

    if save_json:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        out = {
            "scraped_at": datetime.utcnow().isoformat(),
            "source":     LISTING_URL,
            "state":      STATE,
            "total":      len(grants),
            "grants":     grants,
        }
        with open(OUTPUT_FILE, "w") as f:
            json.dump(out, f, indent=2, default=str)
        log.info(f"\nSaved {len(grants)} grants → {OUTPUT_FILE}")

    duration = (datetime.now() - start).total_seconds()
    active   = [g for g in grants if g.get("status") == "active"]
    log.info(f"Done in {duration:.1f}s — {len(grants)} grants  ({len(active)} active)")
    return grants


if __name__ == "__main__":
    run(save_json=True)
