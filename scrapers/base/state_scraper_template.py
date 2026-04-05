"""
State scraper template — copy and fill in CONFIG for each new state/source.
The scraping logic stays the same; only the config values change.
"""

import os
import sys
import re
import json
import hashlib
import logging
from datetime import datetime
from typing import List, Dict, Optional
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base_scraper import (
    safe_get, extract_pdf_text, extract_date, extract_amount,
    ai_extract, clean_and_validate, load_to_db, log, _cache,
)

# CONFIG — change this section for each new state/source

CONFIG = {
    "scraper_id":   "ny_esd",
    "state":        "NY",
    "listing_url":  "https://esd.ny.gov/doing-business-ny/funding-opportunities",
    "base_url":     "https://esd.ny.gov",
    "domain":       "esd.ny.gov",
    "output_file":  "ny_esd_grants_raw.json",
    "grant_keywords": [
        "grant", "funding", "opportunity", "program", "award",
        "loan", "incentive", "assistance", "support",
    ],
    # links matching these are skipped
    "skip_keywords": [
        "login", "contact", "about", "news", "events", "sitemap",
        "privacy", "facebook", "twitter", "linkedin", "youtube",
        "careers", "press", "media", "faq",
    ],
}


def scrape_listing(config: Dict) -> List[Dict]:
    """Layer 1 — Collect all grant links from the listing page or sitemap."""
    listing_url = config["listing_url"]
    log.info(f"LAYER 1 — {config['state']}: {listing_url}")

    links = []
    seen  = set()

    # Sitemap discovery
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

                    if url.endswith(".xml"):  # nested sitemap index
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

    # Seed URLs (hardcoded known grant pages)
    for seed_url in config.get("seed_urls", []):
        if seed_url not in seen:
            seen.add(seed_url)
            name = seed_url.rstrip("/").split("/")[-1].replace("-", " ").title()
            links.append({"url": seed_url, "name": name})
            log.info(f"  Seed: {name}")

    # Standard HTML listing page
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


def scrape_page(link_info: Dict, config: Dict, no_cache: bool = False) -> Optional[Dict]:
    """Layer 2 — Visit one grant page, extract HTML text and PDF links.
    Returns a dict with _content_hash so callers can update the cache.
    Returns {"_from_cache": True, "_cached_grant": <grant>} on cache hit.
    """
    url  = link_info["url"]
    name = link_info["name"]

    log.info(f"LAYER 2 — {name[:60]}")

    r = safe_get(url)
    if not r:
        return None

    content_hash = _cache.content_hash(r.content)

    # Cache hit — skip PDF download + AI entirely
    if not no_cache:
        cached = _cache.get_if_fresh(url, content_hash)
        if cached:
            log.info(f"  CACHE HIT (unchanged): {name[:60]}")
            return {"_from_cache": True, "_cached_grant": cached}

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
        "url":           url,
        "name":          name,
        "page_text":     page_text,
        "pdf_links":     pdf_links,
        "html_date":     extract_date(page_text),
        "html_amount":   extract_amount(page_text),
        "_content_hash": content_hash,
    }


def process_pdfs(page_data: Dict) -> Dict:
    """Layer 3 — Download and extract text from every PDF on the page (concurrent)."""
    pdf_links = page_data.get("pdf_links", [])
    if not pdf_links:
        page_data["pdf_extractions"] = []
        return page_data

    def _download_one(pdf: Dict) -> Optional[Dict]:
        log.info(f"  PDF: {pdf['label'][:50]}")
        text = extract_pdf_text(pdf["url"])
        if not text:
            return None
        return {
            "url":    pdf["url"],
            "label":  pdf["label"],
            "text":   text,
            "date":   extract_date(text),
            "amount": extract_amount(text),
        }

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(_download_one, pdf_links))

    page_data["pdf_extractions"] = [r for r in results if r]
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
    final["combined_text"] = merged.get("combined_text", "")

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
        load_db: bool = False, db_session=None,
        no_cache: bool = False) -> List[Dict]:
    """Full pipeline for one state source. Processes detail pages concurrently."""
    log.info("=" * 70)
    log.info(f"SCRAPER STARTING — {config['state']} | {config['listing_url']}")
    log.info(f"=" * 70)

    start = datetime.now()
    links = scrape_listing(config)

    def _process_one(link: Dict) -> Optional[Dict]:
        try:
            page = scrape_page(link, config, no_cache=no_cache)
            if not page:
                return None

            # Cache hit — return immediately, skip expensive steps
            if page.get("_from_cache"):
                return page["_cached_grant"]

            content_hash = page.pop("_content_hash", None)

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

            if content_hash:
                _cache.set(link["url"], content_hash, cleaned)

            return cleaned
        except Exception as exc:
            log.error(f"  Error processing {link.get('url', '?')}: {exc}")
            return None

    # Process all detail pages concurrently (5 workers)
    grants = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_process_one, link): link for link in links}
        for future in as_completed(futures):
            result = future.result()
            if result:
                grants.append(result)

    # Persist cache to disk after each source completes
    _cache.flush()

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