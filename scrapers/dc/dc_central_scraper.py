"""
DC Central Grants Scraper
Source: https://dc.gov/page/grants-and-funding

Scrapes the DC.gov Grants & Funding hub page and follows agency-specific
grant pages that are NOT already covered by other DC scrapers (OVSJG,
DSLBD, DMPED, DOES, OSSE).

Agencies covered here:
  - DC Office of Planning — Historic Homeowner Grant Program
  - DC Developmental Disabilities Council (DDC)
  - Mayor's Office of Community Affairs (MOCA) — Community Grant Program
  - Mayor's Office on LGBTQ Affairs — LGBTQ Community Grant
  - DC Office of Latino Affairs (MOLA)
  - MOCA AmeriCorps grants (communityaffairs.dc.gov)

Hub URL is used as the source listing page; each agency page becomes
one or more grant records.

Output: data/dc/dc_central_grants_raw.json
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
DATA_DIR     = PROJECT_ROOT / "data" / "dc"

sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(APP_DIR))

from base_scraper import (
    safe_get, extract_pdf_text, extract_date, extract_amount,
    ai_extract, clean_and_validate, log, _cache,
)

STATE       = "DC"
HUB_URL     = "https://dc.gov/page/grants-and-funding"
OUTPUT_FILE = str(DATA_DIR / "dc_central_grants_raw.json")

# Already covered by other DC scrapers — don't re-scrape
ALREADY_SCRAPED_DOMAINS = {
    "ovsjg.dc.gov", "dslbd.dc.gov", "dmped.dc.gov",
    "does.dc.gov", "osse.dc.gov", "dcgrants.dc.gov",
}

# Seed pages not directly discoverable from the hub but known to have grants
SEED_PAGES = [
    {
        "title":   "Historic Homeowner Grant Program",
        "url":     "https://planning.dc.gov/service/historic-homeowner-grant-program",
        "agency":  "DC Office of Planning",
    },
    {
        "title":   "DC Developmental Disabilities Council Funding Opportunities",
        "url":     "https://ddc.dc.gov/service/funding-opportunities-page",
        "agency":  "DC Developmental Disabilities Council",
    },
    {
        "title":   "MOCA Community Grant Program",
        "url":     "https://communityaffairs.dc.gov/content/community-grant-program",
        "agency":  "Mayor's Office of Community Affairs",
    },
    {
        "title":   "LGBTQ Community Grant",
        "url":     "https://dc.gov/service/lgbtq-community-grant",
        "agency":  "Mayor's Office on LGBTQ Affairs",
    },
    {
        "title":   "DC Office on Latino Affairs — Grant Opportunities",
        "url":     "https://communityaffairs.dc.gov/mola",
        "agency":  "Mayor's Office on Latino Affairs",
    },
]

GRANT_KEYWORDS = {
    "grant", "fund", "award", "opportunity", "assistance",
    "program", "support", "loan", "incentive", "scholarship",
}
SKIP_KEYWORDS = {
    "login", "logout", "contact", "sitemap", "privacy",
    "facebook", "twitter", "linkedin", "youtube", "careers",
    "news", "press", "events", "faq", "about", "subscribe",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def scrape_hub() -> List[Dict]:
    """
    Layer 1 — Return curated seed pages for DC agency grant programs.

    The dc.gov/page/grants-and-funding hub is too generic (navigation links,
    readspeaker buttons, and employment service pages all pass a naive keyword
    filter). Instead we use a curated SEED_PAGES list of specific DC agency
    grant pages that are not already covered by other DC scrapers.
    """
    log.info(f"LAYER 1 — Seeded from hub: {HUB_URL}")
    items = []
    seen  = set()
    for seed in SEED_PAGES:
        if seed["url"] not in seen:
            seen.add(seed["url"])
            items.append(seed)
            log.info(f"  Seed: {seed['title'][:70]}")
    log.info(f"LAYER 1 complete — {len(items)} candidate pages")
    return items


def scrape_detail(item: Dict) -> Optional[Dict]:
    """Layer 2 — Fetch one agency/service page."""
    url   = item["url"]
    title = item["title"]
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

    # Look for an apply link on this page
    application_url = None
    for a in soup.find_all("a", href=True):
        label = a.get_text(strip=True).lower()
        href  = a["href"].strip()
        if any(kw in label for kw in ["apply", "application", "submit", "zoomgrant", "form"]):
            if href.startswith("http") and href != url:
                application_url = href
                break
            elif not href.startswith("http"):
                candidate = urljoin(url, href)
                if candidate != url:
                    application_url = candidate
                    break

    return {
        "url":             url,
        "name":            title,
        "agency":          item.get("agency", ""),
        "page_text":       page_text,
        "pdf_links":       pdf_links,
        "application_url": application_url,
        "html_date":       extract_date(page_text),
        "html_amount":     extract_amount(page_text),
        "_content_hash":   chash,
    }


def build_grant(page: Dict) -> Dict:
    """Merge page data and run AI enrichment."""
    conf_rank = {"high": 3, "medium": 2, "low": 1}

    texts = [page.get("page_text", "")]
    for pdf in page.get("pdf_links", []):
        txt = extract_pdf_text(pdf["url"])
        if txt:
            texts.append(txt)
    combined_text = "\n\n--- PDF ---\n\n".join(texts)

    # Best date
    date_candidates = []
    hd = page.get("html_date", {})
    if hd and (hd.get("deadline") or hd.get("rolling")):
        date_candidates.append(hd)

    deadline, is_rolling = None, False
    if date_candidates:
        best = max(date_candidates, key=lambda x: conf_rank.get(x.get("confidence", "low"), 0))
        deadline   = best.get("deadline")
        is_rolling = best.get("rolling", False)

    # Best amount
    ha = page.get("html_amount", {}) or {}
    award_min = ha.get("award_min")
    award_max = ha.get("award_max")

    source_url = page["url"]
    ai = ai_extract(combined_text, source_url, STATE) or {}
    final = {**ai}
    final["combined_text"] = combined_text

    if not final.get("title"):
        final["title"] = page["name"]
    if not final.get("deadline") and deadline:
        final["deadline"] = deadline
    if not final.get("rolling") and is_rolling:
        final["rolling"] = True
    if not final.get("award_min") and award_min:
        final["award_min"] = award_min
    if not final.get("award_max") and award_max:
        final["award_max"] = award_max

    agency = page.get("agency", "")
    if not final.get("description") and agency:
        final["description"] = f"{agency}: {page['name']}."

    final["opportunity_url"] = source_url
    if not final.get("application_url"):
        final["application_url"] = page.get("application_url") or source_url

    return final


def run(save_json: bool = True) -> List[Dict]:
    log.info("=" * 70)
    log.info(f"DC CENTRAL GRANTS SCRAPER — {HUB_URL}")
    log.info("=" * 70)
    start = datetime.now()

    items  = scrape_hub()
    grants      = []
    seen_titles = set()   # dedup by title to catch same grant from multiple pages

    def _process_one(item):
        try:
            page = scrape_detail(item)
            if not page:
                return None
            if page.get("_from_cache"):
                return page["_cached_grant"]

            chash = page.pop("_content_hash", None)
            final   = build_grant(page)
            cleaned = clean_and_validate(final, STATE, item["url"])
            if chash:
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
                    title_key = (cleaned.get("title") or "").strip().lower()[:80]
                    if title_key in seen_titles:
                        log.info(f"  [skip dup] {title_key[:60]}")
                        continue
                    seen_titles.add(title_key)
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
            "source":     HUB_URL,
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
