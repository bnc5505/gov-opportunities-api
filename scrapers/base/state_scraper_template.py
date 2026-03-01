"""
State Scraper Template
──────────────────────
Copy this file and fill in the CONFIG section for each new state/source.
The scraping logic stays the same for every state — only the config changes.

States planned:
  PA  →  pa_dced_scraper.py          (already built — full deep scraper)
  DC  →  dc_ovsjg_scraper.py         (already built — PDF focused)
  NY  →  ny_esd_scraper.py           (use this template)
  MD  →  md_commerce_scraper.py      (use this template)

For each new source, copy this file, fill in CONFIG, and run.
"""

import os
import sys
import json
import logging
from datetime import datetime
from typing import List, Dict, Optional
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base_scraper import (
    safe_get, extract_pdf_text, extract_date, extract_amount,
    ai_extract, clean_and_validate, load_to_db, log,
)

# ═══════════════════════════════════════════════════════════════════
#  CONFIG — Change this section for each new state / source
# ═══════════════════════════════════════════════════════════════════

CONFIG = {
    # Unique identifier for this scraper
    "scraper_id":   "ny_esd",

    # State code
    "state":        "NY",

    # The main listing page where grants are listed
    "listing_url":  "https://esd.ny.gov/doing-business-ny/funding-opportunities",

    # Base domain — used to resolve relative links
    "base_url":     "https://esd.ny.gov",

    # Domain filter — only follow links from this domain
    "domain":       "esd.ny.gov",

    # Output JSON file name
    "output_file":  "ny_esd_grants_raw.json",

    # Words that must appear in a link URL or text for it to look like a grant
    "grant_keywords": [
        "grant", "funding", "opportunity", "program", "award",
        "loan", "incentive", "assistance", "support",
    ],

    # Words that indicate a link is NOT a grant page — skip these
    "skip_keywords": [
        "login", "contact", "about", "news", "events", "sitemap",
        "privacy", "facebook", "twitter", "linkedin", "youtube",
        "careers", "press", "media", "faq",
    ],
}


# ═══════════════════════════════════════════════════════════════════
#  SCRAPER — Same logic for every state
# ═══════════════════════════════════════════════════════════════════

def scrape_listing(config: Dict) -> List[Dict]:
    """Layer 1 — Collect all grant links from the listing page or sitemap."""
    listing_url = config["listing_url"]
    log.info(f"LAYER 1 — {config['state']}: {listing_url}")

    links = []
    seen  = set()

    # ── Handle sitemap.xml discovery ──────────────────────────────
    if listing_url.endswith(".xml"):
        r = safe_get(listing_url)
        if r:
            try:
                # Remove XML namespaces before parsing — they break ElementTree
                xml_text = re.sub(r'\s+xmlns[^"]*"[^"]*"', '', r.text)
                xml_text = re.sub(r'\s+xmlns[^=]*=["\'][^"\']*["\']', '', xml_text)

                # Try ElementTree first
                try:
                    import xml.etree.ElementTree as ET
                    root = ET.fromstring(xml_text.encode("utf-8"))
                    raw_urls = [loc.text for loc in root.iter("loc") if loc.text]
                except Exception:
                    # Fallback — plain regex on raw text (catches malformed XML too)
                    raw_urls = re.findall(r"<loc>\s*(https?://[^\s<]+)\s*</loc>", r.text)

                lower_grant_kw = config["grant_keywords"]
                lower_skip_kw  = config["skip_keywords"]

                for url in raw_urls:
                    url = url.strip()
                    if not url:
                        continue

                    # If this is a nested sitemap index, fetch it too
                    if url.endswith(".xml"):
                        sub_r = safe_get(url)
                        if sub_r:
                            sub_urls = re.findall(
                                r"<loc>\s*(https?://[^\s<]+)\s*</loc>",
                                sub_r.text
                            )
                            raw_urls.extend(sub_urls)
                        continue

                    lower_url = url.lower()
                    if any(kw in lower_url for kw in lower_skip_kw):
                        continue
                    if any(kw in lower_url for kw in lower_grant_kw):
                        name = url.rstrip("/").split("/")[-1].replace("-", " ").title()
                        if url not in seen:
                            seen.add(url)
                            links.append({"url": url, "name": name})

                log.info(f"  Sitemap yielded {len(links)} candidate pages")

            except Exception as e:
                log.warning(f"  Sitemap parse failed: {e} — falling back to seed URLs only")

    # ── Handle seed_urls (hardcoded known grant pages) ─────────────
    for seed_url in config.get("seed_urls", []):
        if seed_url not in seen:
            seen.add(seed_url)
            name = seed_url.rstrip("/").split("/")[-1].replace("-", " ").title()
            links.append({"url": seed_url, "name": name})
            log.info(f"  Seed: {name}")

    # ── Standard HTML listing page ─────────────────────────────────
    if not listing_url.endswith(".xml"):
        r = safe_get(listing_url)
        if not r:
            log.error("Could not fetch listing page")
            if not links:
                return []
        else:
            soup = BeautifulSoup(r.content, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                text = a.get_text(strip=True)

                if not text or len(text) < 5:
                    continue
                if href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:"):
                    continue
                if not href.startswith("http"):
                    href = urljoin(config["base_url"], href)

                parsed = urlparse(href)
                if config["domain"] not in parsed.netloc:
                    continue
                if href.rstrip("/") == listing_url.rstrip("/"):
                    continue

                lower_href = href.lower()
                lower_text = text.lower()

                if any(kw in lower_href or kw in lower_text for kw in config["skip_keywords"]):
                    continue

                has_grant_kw = any(
                    kw in lower_href or kw in lower_text
                    for kw in config["grant_keywords"]
                )
                if not has_grant_kw:
                    continue

                if href not in seen:
                    seen.add(href)
                    links.append({"url": href, "name": text})
                    log.info(f"  Found: {text[:70]}")

    log.info(f"LAYER 1 complete — {len(links)} pages found")
    return links


def scrape_page(link_info: Dict, config: Dict) -> Optional[Dict]:
    """Layer 2 — Visit one grant page, extract HTML text and PDF links."""
    url  = link_info["url"]
    name = link_info["name"]

    log.info(f"LAYER 2 — {name[:60]}")

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
                href = urljoin(config["base_url"], href)
            if href not in seen_pdfs:
                seen_pdfs.add(href)
                pdf_links.append({"url": href, "label": a.get_text(strip=True)})
                log.info(f"  PDF: {a.get_text(strip=True)[:60]}")

    return {
        "url":         url,
        "name":        name,
        "page_text":   page_text,
        "pdf_links":   pdf_links,
        "html_date":   extract_date(page_text),
        "html_amount": extract_amount(page_text),
    }


def process_pdfs(page_data: Dict) -> Dict:
    """Layer 3 — Download and extract text from every PDF on the page."""
    pdf_extractions = []
    for pdf in page_data.get("pdf_links", []):
        log.info(f"  PDF: {pdf['label'][:50]}")
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
    page_data["pdf_extractions"] = pdf_extractions
    return page_data


def merge(page_data: Dict) -> Dict:
    """Merge HTML and PDF extractions — best data wins."""
    conf_rank = {"high": 3, "medium": 2, "low": 1}

    merged = {
        "title":          page_data["name"],
        "source_url":     page_data["url"],
        "deadline":       None,
        "rolling":        False,
        "is_annual":      False,
        "award_min":      None,
        "award_max":      None,
        "total_funding":  None,
        "award_text":     None,
        "combined_text":  "",
        "needs_review":   True,
    }

    # Combined text for AI
    texts = [page_data.get("page_text", "")]
    for pdf in page_data.get("pdf_extractions", []):
        texts.append(pdf.get("text", ""))
    merged["combined_text"] = "\n\n--- PDF ---\n\n".join(texts)

    # Best date
    date_candidates = []
    if page_data.get("html_date", {}).get("deadline") or page_data.get("html_date", {}).get("rolling"):
        date_candidates.append(page_data["html_date"])
    for pdf in page_data.get("pdf_extractions", []):
        d = pdf.get("date", {})
        if d.get("deadline") or d.get("rolling"):
            date_candidates.append(d)

    if date_candidates:
        best = max(date_candidates, key=lambda x: conf_rank.get(x.get("confidence", "low"), 0))
        merged.update(
            deadline=best.get("deadline"),
            rolling=best.get("rolling", False),
            is_annual=best.get("is_annual", False),
            needs_review=best.get("needs_review", False),
        )

    # Best amount
    amount_candidates = []
    ha = page_data.get("html_amount", {})
    if ha.get("award_max") or ha.get("award_min"):
        amount_candidates.append(ha)
    for pdf in page_data.get("pdf_extractions", []):
        a = pdf.get("amount", {})
        if a.get("award_max") or a.get("award_min"):
            amount_candidates.append(a)

    if amount_candidates:
        best = max(
            amount_candidates,
            key=lambda x: sum(1 for v in [x.get("award_min"), x.get("award_max"), x.get("total_funding")] if v)
        )
        merged.update(
            award_min=best.get("award_min"),
            award_max=best.get("award_max"),
            total_funding=best.get("total_funding"),
            award_text=best.get("award_text"),
        )

    return merged


def final_ai_pass(merged: Dict, state: str) -> Dict:
    """Final Azure AI extraction to fill everything regex missed."""
    ai = ai_extract(merged["combined_text"], merged["source_url"], state)
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
        final["application_url"] = merged["source_url"]

    return final


def run(config: Dict = CONFIG, save_json: bool = True,
        load_db: bool = False, db_session=None) -> List[Dict]:
    """Full pipeline for one state source."""
    log.info("=" * 70)
    log.info(f"SCRAPER STARTING — {config['state']} | {config['listing_url']}")
    log.info("=" * 70)

    start  = datetime.now()
    links  = scrape_listing(config)
    grants = []

    for i, link in enumerate(links, 1):
        log.info(f"\n{'─'*60}")
        log.info(f"{i}/{len(links)}: {link['name'][:60]}")

        page = scrape_page(link, config)
        if not page:
            continue

        page    = process_pdfs(page)
        merged  = merge(page)
        final   = final_ai_pass(merged, config["state"])
        cleaned = clean_and_validate(final, config["state"], link["url"])

        log.info(
            f"  deadline: {cleaned.get('deadline')} | "
            f"award_max: {cleaned.get('award_max')} | "
            f"quality: {cleaned.get('data_quality_score')} | "
            f"status: {cleaned.get('status')}"
        )
        grants.append(cleaned)

    if save_json:
        out = {
            "scraped_at": datetime.utcnow().isoformat(),
            "source":     config["listing_url"],
            "state":      config["state"],
            "total":      len(grants),
            "grants":     grants,
        }
        with open(config["output_file"], "w") as f:
            json.dump(out, f, indent=2, default=str)
        log.info(f"Saved {len(grants)} grants → {config['output_file']}")

    if load_db and db_session:
        stats = load_to_db(grants, db_session)
        log.info(f"DB: saved={stats['saved']} skipped={stats['skipped']} errors={stats['errors']}")

    duration = (datetime.now() - start).total_seconds()
    log.info(f"\nDone in {duration:.1f}s — {len(grants)} grants")
    return grants


if __name__ == "__main__":
    grants = run(save_json=True, load_db=False)
    print(f"\nDone. {len(grants)} grants. Saved to {CONFIG['output_file']}")