"""
New York State Council on the Arts — Grants Scraper
Source: https://www.nysca.org/apply/

Scrapes NYSCA arts and cultural grant programs, follows each detail page,
runs Azure AI enrichment, saves to JSON.
"""

import os
import sys
import json
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

THIS_DIR     = Path(__file__).resolve().parent
PROJECT_ROOT = THIS_DIR.parent.parent
BASE_DIR     = PROJECT_ROOT / "scrapers" / "base"
APP_DIR      = PROJECT_ROOT / "app"
DATA_DIR     = PROJECT_ROOT / "data" / "ny"

sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(APP_DIR))

from base_scraper import (
    safe_get, extract_pdf_text, extract_date, extract_amount,
    ai_extract, clean_and_validate, log,
)

STATE       = "NY"
BASE_URL    = "https://www.nysca.org"
LISTING_URL = "https://www.nysca.org/apply/"
OUTPUT_FILE = str(DATA_DIR / "ny_nysca_grants_raw.json")

SKIP_FRAGMENTS = {"#", "javascript:", "mailto:", "tel:"}
SKIP_KEYWORDS  = {
    "login", "logout", "contact", "sitemap", "privacy", "accessibility",
    "facebook", "twitter", "linkedin", "youtube", "instagram",
    "careers", "news", "press", "events", "about",
}

GRANT_PATH_SEGMENTS = [
    "/apply/", "/grant", "/fund", "/award", "/program",
    "/disciplines/", "/eligibility/", "/guideline",
]

GRANT_TEXT_SIGNAL = {
    "grant", "fund", "award", "program", "opportunity",
    "arts", "cultural", "music", "dance", "theater", "film",
    "literature", "media", "folk", "traditional", "organization",
    "individual", "artist", "residency",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)



def scrape_listing() -> List[Dict]:
    log.info(f"LAYER 1 — Listing: {LISTING_URL}")
    r = safe_get(LISTING_URL)
    if not r:
        log.error("Could not fetch listing page")
        return []

    soup  = BeautifulSoup(r.content, "html.parser")
    links = []
    seen  = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = a.get_text(strip=True)

        if not text or len(text) < 5:
            continue
        if any(href.startswith(f) for f in SKIP_FRAGMENTS):
            continue
        if any(kw in href.lower() for kw in SKIP_KEYWORDS):
            continue

        if not href.startswith("http"):
            href = urljoin(BASE_URL, href)

        parsed = urlparse(href)
        if "nysca.org" not in parsed.netloc:
            continue

        path_lower = parsed.path.lower()
        if path_lower.rstrip("/") in ("", "/apply"):
            continue

        text_lower = text.lower()
        in_grant_path  = any(seg in path_lower for seg in GRANT_PATH_SEGMENTS)
        has_text_signal = any(w in text_lower for w in GRANT_TEXT_SIGNAL)

        if not in_grant_path and not has_text_signal:
            continue

        if href not in seen:
            seen.add(href)
            links.append({"url": href, "name": text})
            log.info(f"  Found: {text[:80]}")

    log.info(f"LAYER 1 complete — {len(links)} grant pages found")
    return links



def scrape_detail(link: Dict) -> Optional[Dict]:
    url  = link["url"]
    name = link["name"]
    log.info(f"LAYER 2 — {name[:70]}")

    r = safe_get(url)
    if not r:
        return None

    soup      = BeautifulSoup(r.content, "html.parser")
    page_text = soup.get_text(separator="\n", strip=True)

    pdf_links = []
    seen_pdfs = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().endswith(".pdf"):
            if not href.startswith("http"):
                href = urljoin(BASE_URL, href)
            if href not in seen_pdfs:
                seen_pdfs.add(href)
                pdf_links.append({"url": href, "label": a.get_text(strip=True)})
                log.info(f"    PDF: {a.get_text(strip=True)[:60]}")

    application_url = None
    for a in soup.find_all("a", href=True):
        label = a.get_text(strip=True).lower()
        href  = a["href"].strip()
        if any(kw in label for kw in ["apply", "application", "submit", "portal", "nyfa source"]):
            if href.startswith("http") and href != url:
                application_url = href
                break
            elif not href.startswith("http"):
                candidate = urljoin(BASE_URL, href)
                if candidate != url:
                    application_url = candidate
                    break

    return {
        "url":             url,
        "name":            name,
        "page_text":       page_text,
        "pdf_links":       pdf_links,
        "application_url": application_url,
        "html_date":       extract_date(page_text),
        "html_amount":     extract_amount(page_text),
    }



def process_pdfs(page: Dict) -> Dict:
    pdf_extractions = []
    for pdf in page.get("pdf_links", []):
        log.info(f"    Extracting PDF: {pdf['label'][:50]}")
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



def merge(page: Dict) -> Dict:
    conf_rank = {"high": 3, "medium": 2, "low": 1}

    merged = {
        "title":           page["name"],
        "source_url":      page["url"],
        "application_url": page.get("application_url") or page["url"],
        "deadline":        None,
        "rolling":         False,
        "is_annual":       False,
        "award_min":       None,
        "award_max":       None,
        "total_funding":   None,
        "award_text":      None,
        "combined_text":   "",
        "needs_review":    True,
    }

    texts = [page.get("page_text", "")]
    for pdf in page.get("pdf_extractions", []):
        texts.append(pdf.get("text", ""))
    merged["combined_text"] = "\n\n--- PDF ---\n\n".join(texts)

    date_candidates = []
    hd = page.get("html_date", {})
    if hd and (hd.get("deadline") or hd.get("rolling")):
        date_candidates.append(hd)
    for pdf in page.get("pdf_extractions", []):
        d = pdf.get("date", {})
        if d and (d.get("deadline") or d.get("rolling")):
            date_candidates.append(d)

    if date_candidates:
        best = max(date_candidates, key=lambda x: conf_rank.get(x.get("confidence", "low"), 0))
        merged.update(
            deadline     = best.get("deadline"),
            rolling      = best.get("rolling", False),
            is_annual    = best.get("is_annual", False),
            needs_review = best.get("needs_review", False),
        )

    amount_candidates = []
    ha = page.get("html_amount", {})
    if ha and (ha.get("award_max") or ha.get("award_min")):
        amount_candidates.append(ha)
    for pdf in page.get("pdf_extractions", []):
        a = pdf.get("amount", {})
        if a and (a.get("award_max") or a.get("award_min")):
            amount_candidates.append(a)

    if amount_candidates:
        best = max(
            amount_candidates,
            key=lambda x: sum(1 for v in [x.get("award_min"), x.get("award_max"), x.get("total_funding")] if v)
        )
        merged.update(
            award_min     = best.get("award_min"),
            award_max     = best.get("award_max"),
            total_funding = best.get("total_funding"),
            award_text    = best.get("award_text"),
        )

    return merged



def ai_pass(merged: Dict) -> Dict:
    ai = ai_extract(merged["combined_text"], merged["source_url"], STATE)
    if not ai:
        return merged

    final = {**ai}

    if merged.get("deadline") and not ai.get("deadline"):
        final["deadline"] = merged["deadline"]
    if merged.get("award_max") and not ai.get("award_max"):
        final["award_max"] = merged["award_max"]
    if merged.get("award_min") and not ai.get("award_min"):
        final["award_min"] = merged["award_min"]
    if merged.get("is_annual"):
        final["is_annual"] = True
        final["rolling"]   = True

    final["opportunity_url"] = merged["source_url"]
    if not final.get("application_url"):
        final["application_url"] = merged.get("application_url") or merged["source_url"]

    return final



def run(save_json: bool = True) -> List[Dict]:
    log.info("=" * 70)
    log.info(f"NY STATE COUNCIL ON THE ARTS SCRAPER — {LISTING_URL}")
    log.info("=" * 70)
    start = datetime.now()

    links  = scrape_listing()
    grants = []

    for i, link in enumerate(links, 1):
        log.info(f"\n{'─'*60}")
        log.info(f"{i}/{len(links)}: {link['name'][:65]}")

        page = scrape_detail(link)
        if not page:
            continue

        page    = process_pdfs(page)
        merged  = merge(page)
        final   = ai_pass(merged)
        cleaned = clean_and_validate(final, STATE, link["url"])

        log.info(
            f"  deadline={cleaned.get('deadline')}  "
            f"award_max={cleaned.get('award_max')}  "
            f"score={cleaned.get('data_quality_score')}  "
            f"status={cleaned.get('status')}"
        )
        grants.append(cleaned)
        time.sleep(0.5)

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
