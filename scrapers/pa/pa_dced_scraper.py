"""
Pennsylvania DCED Scraper
Source: https://dced.pa.gov/programs/

Layer 1 — Collect all grant/program links from the main listing page
Layer 2 — Visit each grant page, extract HTML data + collect all PDF links
Layer 3 — Download every PDF, extract text, run intelligent extraction
Final    — Azure AI pass to fill any gaps, then clean and load to DB
"""

import os
import sys
import json
import time
import logging
from datetime import datetime
from typing import List, Dict, Optional
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

# Import base scraper utilities — base/ is one level up from pa/
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent / "base"))
from base_scraper import (
    safe_get, extract_pdf_text, extract_date, extract_amount,
    ai_extract, clean_and_validate, load_to_db, log,
)

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────
STATE       = "PA"
BASE_URL    = "https://dced.pa.gov"
LISTING_URL = "https://dced.pa.gov/programs/"
_DATA_DIR   = str(_Path(__file__).resolve().parent.parent.parent / "data" / "pa")
OUTPUT_FILE = os.path.join(_DATA_DIR, "pa_dced_grants_raw.json")


# ═══════════════════════════════════════════════════════════════════
#  LAYER 1 — Collect all program links from main listing page
# ═══════════════════════════════════════════════════════════════════

def scrape_listing_page() -> List[Dict]:
    """
    Goes to dced.pa.gov/programs/ and collects every program/grant link.
    Returns list of {url, name} dicts.
    """
    log.info(f"LAYER 1 — Fetching listing page: {LISTING_URL}")

    r = safe_get(LISTING_URL)
    if not r:
        log.error("Could not fetch listing page")
        return []

    soup  = BeautifulSoup(r.content, "html.parser")
    links = []
    seen  = set()

    # DCED actual grant/program pages always follow the pattern:
    # https://dced.pa.gov/programs/program-name/
    # We ONLY want these — not nav links, not section pages
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = a.get_text(strip=True)

        if not text or len(text) < 5:
            continue
        if href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:"):
            continue
        if not href.startswith("http"):
            href = urljoin(BASE_URL, href)

        parsed = urlparse(href)

        # Only keep dced.pa.gov links
        if "dced.pa.gov" not in parsed.netloc:
            continue

        # Only keep URLs that are actual program detail pages
        # Real programs look like: /programs/abandoned-mine-drainage/
        # NOT like: /library/ or /why-pa/ or /pennsylvania/
        path = parsed.path.rstrip("/")
        path_parts = [p for p in path.split("/") if p]

        # Must have at least 2 path parts and first part must be "programs"
        if len(path_parts) < 2 or path_parts[0] != "programs":
            continue

        # Skip the /programs/ root page itself
        if len(path_parts) == 1:
            continue

        # Skip known non-grant sections
        skip_slugs = [
            "archived-programs", "compliance-resources", "how-to-apply",
            "quality-assurance", "investment-tracker", "certified-economic",
            "coal-plant-redevelopment", "qualified-opportunity-zones",
        ]
        if any(slug in path for slug in skip_slugs):
            continue

        if href not in seen:
            seen.add(href)
            links.append({"url": href, "name": text})
            log.info(f"  Found grant: {text[:70]}")

    log.info(f"LAYER 1 complete — {len(links)} program pages found")
    return links


# ═══════════════════════════════════════════════════════════════════
#  LAYER 2 — Visit each grant page, extract HTML data + PDF links
# ═══════════════════════════════════════════════════════════════════

def scrape_grant_page(link_info: Dict) -> Optional[Dict]:
    """
    Visits one grant/program page.
    Extracts:
      - All visible text from the page
      - Any structured data visible in the HTML (amount, deadline, etc.)
      - All PDF links on the page
    """
    url  = link_info["url"]
    name = link_info["name"]

    log.info(f"\nLAYER 2 — Scraping: {name[:70]}")
    log.info(f"  URL: {url}")

    r = safe_get(url)
    if not r:
        log.warning(f"  Could not fetch page: {url}")
        return None

    soup      = BeautifulSoup(r.content, "html.parser")
    page_text = soup.get_text(separator="\n", strip=True)

    # Collect all PDF links on this page
    pdf_links = []
    seen_pdfs = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().endswith(".pdf"):
            if not href.startswith("http"):
                href = urljoin(BASE_URL, href)
            if href not in seen_pdfs:
                seen_pdfs.add(href)
                pdf_links.append({
                    "url":   href,
                    "label": a.get_text(strip=True),
                })
                log.info(f"  PDF found: {a.get_text(strip=True)[:60]}")

    # Also check for links that go to a sub-page that might have more PDFs
    # DCED sometimes nests grants 2 levels deep
    sub_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href.startswith("http"):
            href = urljoin(BASE_URL, href)
        parsed = urlparse(href)
        if (
            "dced.pa.gov" in parsed.netloc
            and href != url
            and not href.lower().endswith(".pdf")
            and "program" in href.lower() or "grant" in href.lower()
        ):
            sub_links.append(href)

    # Quick date + amount extraction from HTML text before PDF pass
    date_result   = extract_date(page_text)
    amount_result = extract_amount(page_text)

    return {
        "url":              url,
        "name":             name,
        "page_text":        page_text,
        "pdf_links":        pdf_links,
        "sub_links":        sub_links[:5],   # limit sub-page depth
        "html_date":        date_result,
        "html_amount":      amount_result,
    }


# ═══════════════════════════════════════════════════════════════════
#  LAYER 3 — Download PDFs, extract text, run intelligent extraction
# ═══════════════════════════════════════════════════════════════════

def process_pdfs(page_data: Dict) -> Dict:
    """
    Downloads every PDF found on the grant page.
    Extracts text from each and runs date + amount extraction.
    Returns enriched page_data with pdf_texts added.
    """
    pdf_extractions = []

    for pdf_info in page_data.get("pdf_links", []):
        log.info(f"  LAYER 3 — Processing PDF: {pdf_info['label'][:60]}")

        text = extract_pdf_text(pdf_info["url"])
        if not text:
            continue

        pdf_date   = extract_date(text)
        pdf_amount = extract_amount(text)

        pdf_extractions.append({
            "url":    pdf_info["url"],
            "label":  pdf_info["label"],
            "text":   text,
            "date":   pdf_date,
            "amount": pdf_amount,
        })
        log.info(
            f"    Extracted {len(text)} chars | "
            f"date: {pdf_date.get('deadline')} | "
            f"amount: {pdf_amount.get('award_max')}"
        )

    page_data["pdf_extractions"] = pdf_extractions
    return page_data


# ═══════════════════════════════════════════════════════════════════
#  MERGE — Combine HTML + PDF data intelligently
# ═══════════════════════════════════════════════════════════════════

def merge_extractions(page_data: Dict) -> Dict:
    """
    Merges data extracted from HTML and all PDFs.
    Applies priority logic:
      - High confidence beats low confidence
      - PDF data beats HTML data for financial details
      - HTML data is used as fallback
    """
    merged = {
        "title":       page_data["name"],
        "source_url":  page_data["url"],
        "deadline":    None,
        "rolling":     False,
        "is_annual":   False,
        "award_min":   None,
        "award_max":   None,
        "total_funding": None,
        "award_text":  None,
        "combined_text": "",
        "needs_review": True,
    }

    # Combine all text for AI pass
    all_texts = [page_data.get("page_text", "")]
    for pdf in page_data.get("pdf_extractions", []):
        all_texts.append(pdf.get("text", ""))
    merged["combined_text"] = "\n\n--- PDF ---\n\n".join(all_texts)

    # ── Best date resolution ─────────────────────────────────────────
    # Collect all date results with their confidence
    confidence_rank = {"high": 3, "medium": 2, "low": 1}
    date_candidates = []

    html_date = page_data.get("html_date", {})
    if html_date.get("deadline") or html_date.get("rolling"):
        date_candidates.append(html_date)

    for pdf in page_data.get("pdf_extractions", []):
        d = pdf.get("date", {})
        if d.get("deadline") or d.get("rolling"):
            date_candidates.append(d)

    if date_candidates:
        best = max(date_candidates, key=lambda x: confidence_rank.get(x.get("confidence", "low"), 0))
        merged["deadline"]     = best.get("deadline")
        merged["rolling"]      = best.get("rolling", False)
        merged["is_annual"]    = best.get("is_annual", False)
        merged["needs_review"] = best.get("needs_review", False)

    # ── Best amount resolution ───────────────────────────────────────
    # Prefer PDFs for financial data — they are more authoritative
    amount_candidates = []

    html_amount = page_data.get("html_amount", {})
    if html_amount.get("award_max") or html_amount.get("award_min"):
        amount_candidates.append(html_amount)

    for pdf in page_data.get("pdf_extractions", []):
        a = pdf.get("amount", {})
        if a.get("award_max") or a.get("award_min"):
            amount_candidates.append(a)

    if amount_candidates:
        # Prefer the one with the most fields populated
        best = max(
            amount_candidates,
            key=lambda x: sum(1 for v in [x.get("award_min"), x.get("award_max"), x.get("total_funding")] if v)
        )
        merged["award_min"]      = best.get("award_min")
        merged["award_max"]      = best.get("award_max")
        merged["total_funding"]  = best.get("total_funding")
        merged["award_text"]     = best.get("award_text")

    return merged


# ═══════════════════════════════════════════════════════════════════
#  FINAL AI PASS — Send everything to Azure AI for final extraction
# ═══════════════════════════════════════════════════════════════════

def final_extraction(merged: Dict) -> Dict:
    """
    Send the combined text to Azure AI.
    AI fills in everything regex missed:
      - Full description and summary
      - Eligibility details
      - Tags, industry, SDG alignment
      - Contact info
      - Validates and corrects date and amount
    """
    log.info(f"  FINAL — Azure AI extraction for: {merged['title'][:60]}")

    ai_result = ai_extract(
        combined_text=merged["combined_text"],
        source_url=merged["source_url"],
        state=STATE,
    )

    if not ai_result:
        log.warning("  AI extraction failed — using regex results only")
        return merged

    # Merge AI result with what we already found
    # Regex high-confidence date/amount takes priority over AI
    final = {**ai_result}

    # Keep regex date if it had high confidence
    if merged.get("deadline") and not ai_result.get("deadline"):
        final["deadline"] = merged["deadline"]

    # Keep regex amounts if AI missed them
    if merged.get("award_max") and not ai_result.get("award_max"):
        final["award_max"] = merged["award_max"]
    if merged.get("award_min") and not ai_result.get("award_min"):
        final["award_min"] = merged["award_min"]

    # Keep annual flag
    if merged.get("is_annual"):
        final["is_annual"] = True
        final["rolling"]   = True

    # Ensure source URL is preserved
    final["opportunity_url"] = merged["source_url"]
    if not final.get("application_url"):
        final["application_url"] = merged["source_url"]

    return final


# ═══════════════════════════════════════════════════════════════════
#  MAIN PIPELINE
# ═══════════════════════════════════════════════════════════════════

def run(save_json: bool = True, load_db: bool = False, db_session=None) -> List[Dict]:
    """
    Full PA DCED scraping pipeline.

    Args:
        save_json:   Save raw extracted grants to JSON file
        load_db:     Load cleaned grants into database
        db_session:  SQLAlchemy session (required if load_db=True)

    Returns:
        List of cleaned grant dicts
    """
    log.info("=" * 70)
    log.info("PENNSYLVANIA DCED GRANT SCRAPER — STARTING")
    log.info(f"Source: {LISTING_URL}")
    log.info("=" * 70)

    start = datetime.now()

    # ── Layer 1: Get all program links ──────────────────────────────
    links = scrape_listing_page()
    if not links:
        log.error("No links found. Exiting.")
        return []

    all_grants = []

    for i, link in enumerate(links, 1):
        log.info(f"\n{'─'*60}")
        log.info(f"Processing {i}/{len(links)}: {link['name'][:60]}")
        log.info(f"{'─'*60}")

        # ── Layer 2: Scrape the grant page ──────────────────────────
        page_data = scrape_grant_page(link)
        if not page_data:
            continue

        # ── Layer 3: Process PDFs ───────────────────────────────────
        page_data = process_pdfs(page_data)

        # ── Merge HTML + PDF extractions ────────────────────────────
        merged = merge_extractions(page_data)

        # ── Final AI pass ───────────────────────────────────────────
        final = final_extraction(merged)

        # ── Clean and validate ──────────────────────────────────────
        clean = clean_and_validate(final, STATE, link["url"])

        # Log what we got
        log.info(
            f"  Result → title: {clean['title'][:50]} | "
            f"deadline: {clean.get('deadline')} | "
            f"award_max: {clean.get('award_max')} | "
            f"quality: {clean.get('data_quality_score')} | "
            f"status: {clean.get('status')}"
        )

        all_grants.append(clean)

    # ── Save to JSON ─────────────────────────────────────────────────
    if save_json:
        os.makedirs(_DATA_DIR, exist_ok=True)
        output = {
            "scraped_at":    datetime.utcnow().isoformat(),
            "source":        LISTING_URL,
            "state":         STATE,
            "total_found":   len(all_grants),
            "grants":        all_grants,
        }
        with open(OUTPUT_FILE, "w") as f:
            json.dump(output, f, indent=2, default=str)
        log.info(f"\nSaved {len(all_grants)} grants to {OUTPUT_FILE}")

    # ── Load to database ──────────────────────────────────────────────
    if load_db and db_session:
        log.info("\nLoading to database...")
        stats = load_to_db(all_grants, db_session)
        log.info(f"DB result: saved={stats['saved']} skipped={stats['skipped']} errors={stats['errors']}")

    # ── Summary ───────────────────────────────────────────────────────
    duration = (datetime.now() - start).total_seconds()
    active   = [g for g in all_grants if g.get("status") == "active"]
    review   = [g for g in all_grants if g.get("needs_review")]

    log.info("\n" + "=" * 70)
    log.info("PA DCED SCRAPER — COMPLETE")
    log.info(f"  Duration:       {duration:.1f} seconds")
    log.info(f"  Total scraped:  {len(all_grants)}")
    log.info(f"  Active grants:  {len(active)}")
    log.info(f"  Needs review:   {len(review)}")
    log.info(f"  Output file:    {OUTPUT_FILE}")
    log.info("=" * 70)

    return all_grants


if __name__ == "__main__":
    # Run standalone without DB
    grants = run(save_json=True, load_db=False)
    print(f"\nDone. {len(grants)} grants extracted.")
    print(f"Output saved to {OUTPUT_FILE}")