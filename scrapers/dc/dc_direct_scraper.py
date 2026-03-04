# DC Direct Scraper
#
# Scrapes grant listings directly from two DC government pages that allow
# automated access (return HTTP 200 with a browser User-Agent):
#
#   1. OVSJG  — https://ovsjg.dc.gov/page/funding-opportunities-current
#   2. DC DoH — https://doh.dc.gov/page/grant-opportunities
#
# Run from project root:
#   python -m app.scrappers.dc_direct_scraper

import os
import sys
import logging
import re
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "app"))

from models import (
    Opportunity, Agency, Source, State, ScrapeLog, ReviewQueue,
    OpportunityType, OpportunityStatus, OpportunityCategory,
)
from database import SessionLocal, engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("dc_direct")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}

SOURCES = [
    {
        "name": "DC OVSJG Funding Opportunities",
        "url": "https://ovsjg.dc.gov/page/funding-opportunities-current",
        "agency_code": "OVSJG",
        "agency_name": "Office of Victim Services and Justice Grants",
        "scraper": "ovsjg",
    },
    {
        "name": "DC Department of Health Grants",
        "url": "https://doh.dc.gov/page/grant-opportunities",
        "agency_code": "DC-DOH",
        "agency_name": "DC Department of Health",
        "scraper": "doh",
    },
]


# Helpers

def fetch(url: str) -> Optional[BeautifulSoup]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        return BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        log.warning("fetch failed %s — %s", url, e)
        return None


def parse_amount(text: str) -> Optional[float]:
    """Extract the first dollar amount from a string."""
    m = re.search(r"\$[\d,]+(?:\.\d+)?", text)
    if not m:
        return None
    return float(m.group().replace("$", "").replace(",", ""))


def parse_deadline(text: str) -> Optional[datetime]:
    try:
        return dateparser.parse(text, fuzzy=True)
    except Exception:
        return None


def already_exists(db, title: str, source_id: int) -> bool:
    return (
        db.query(Opportunity)
        .filter(Opportunity.title == title, Opportunity.source_id == source_id)
        .first()
        is not None
    )


# OVSJG scraper

def scrape_ovsjg(source_id: int, agency_id: int, state_id: int, db) -> list[dict]:
    """
    OVSJG lists grants as plain <p><a href="...">Title</a></p> links.
    Each link points to an RFA (Request for Applications) PDF or page.
    We skip supplemental links (Q&A docs, webinar recordings, previous RFAs).
    """
    base_url = "https://ovsjg.dc.gov"
    index_url = f"{base_url}/page/funding-opportunities-current"
    soup = fetch(index_url)
    if not soup:
        return []

    grants = []

    # OVSJG Drupal page stores content in .field-item div
    field = soup.find("div", class_="field-item")
    if not field:
        field = soup.find("main") or soup

    SKIP_PATTERNS = re.compile(
        r"question|q&|response|webinar|powerpoint|recording|previous|archive|source information|nofa",
        re.I
    )

    RFA_PATTERNS = re.compile(
        r"request for application|rfa|solicitation|rfp|notice of funding|grant program",
        re.I
    )

    seen = set()
    for p in field.find_all("p"):
        a = p.find("a", href=True)
        if not a:
            continue
        title = a.get_text(" ", strip=True)
        if len(title) < 15 or len(title) > 300:
            continue
        if SKIP_PATTERNS.search(title):
            continue
        if title in seen:
            continue
        seen.add(title)

        href = a["href"]
        if not href.startswith("http"):
            href = base_url + href if href.startswith("/") else index_url

        grants.append({
            "title": title,
            "description": title,
            "summary": title,
            "opportunity_url": href,
            "application_url": href,
            "deadline": None,
            "deadline_display": None,
            "award_max": None,
            "award_value": None,
            "source_id": source_id,
            "agency_id": agency_id,
            "state_id": state_id,
            "sponsor_name": "Office of Victim Services and Justice Grants",
            "sponsor_website": base_url,
        })

    log.info("OVSJG: extracted %d grant candidates", len(grants))
    return grants


# DC DoH scraper

def scrape_doh(source_id: int, agency_id: int, state_id: int, db) -> list[dict]:
    """
    DC Department of Health lists grants with links to detail pages.
    """
    index_url = "https://doh.dc.gov/page/grant-opportunities"
    soup = fetch(index_url)
    if not soup:
        return []

    grants = []

    body = soup.find("div", class_=re.compile(r"field--name-body|node__content|region-content"))
    if not body:
        body = soup.find("main") or soup.find("article") or soup

    # Strategy 1: heading-based (same as OVSJG)
    headings = body.find_all(["h2", "h3", "h4"])
    log.info("DoH: found %d headings", len(headings))

    processed_titles = set()

    for h in headings:
        title = h.get_text(" ", strip=True)
        if len(title) < 10 or len(title) > 300:
            continue
        if any(kw in title.lower() for kw in ["navigation", "menu", "breadcrumb", "footer", "sidebar"]):
            continue
        if title in processed_titles:
            continue
        processed_titles.add(title)

        description_parts = []
        deadline_text = None
        apply_url = ""
        opportunity_url = index_url

        sib = h.find_next_sibling()
        while sib and sib.name not in ("h2", "h3", "h4"):
            text = sib.get_text(" ", strip=True)
            if text:
                description_parts.append(text)
                if re.search(r"deadline|due date|closes?|apply by|submission", text, re.I):
                    deadline_text = text
            link = sib.find("a", href=True)
            if link and not apply_url:
                href = link["href"]
                if href.startswith("http"):
                    apply_url = href
                    opportunity_url = href
                elif href.startswith("/"):
                    apply_url = "https://doh.dc.gov" + href
                    opportunity_url = apply_url
            sib = sib.find_next_sibling()

        description = " ".join(description_parts)[:2000]
        summary = description[:300] if description else title
        deadline = parse_deadline(deadline_text) if deadline_text else None
        amount = parse_amount(description)

        grants.append({
            "title": title,
            "description": description,
            "summary": summary,
            "opportunity_url": opportunity_url,
            "application_url": apply_url,
            "deadline": deadline,
            "deadline_display": deadline_text[:100] if deadline_text else None,
            "award_max": amount,
            "award_value": f"${amount:,.0f}" if amount else None,
            "source_id": source_id,
            "agency_id": agency_id,
            "state_id": state_id,
            "sponsor_name": "DC Department of Health",
            "sponsor_website": "https://doh.dc.gov",
        })

    # Strategy 2: if no headings found, try links list
    if not grants:
        for a in body.find_all("a", href=True):
            title = a.get_text(" ", strip=True)
            if len(title) < 15 or len(title) > 300:
                continue
            href = a["href"]
            if href.startswith("/"):
                href = "https://doh.dc.gov" + href
            grants.append({
                "title": title,
                "description": "",
                "summary": title,
                "opportunity_url": href,
                "application_url": href,
                "deadline": None,
                "deadline_display": None,
                "award_max": None,
                "award_value": None,
                "source_id": source_id,
                "agency_id": agency_id,
                "state_id": state_id,
                "sponsor_name": "DC Department of Health",
                "sponsor_website": "https://doh.dc.gov",
            })

    log.info("DoH: extracted %d grant candidates", len(grants))
    return grants


# DB helpers

def get_or_create_source(db, cfg: dict, state_id: int) -> Source:
    source = db.query(Source).filter(Source.url == cfg["url"]).first()
    if not source:
        source = Source(
            name=cfg["name"],
            url=cfg["url"],
            state_id=state_id,
            scraper_type="html",
            scrape_frequency_hours=24,
            is_active=True,
        )
        db.add(source)
        db.flush()
    return source


def get_or_create_agency(db, cfg: dict, state_id: int) -> Agency:
    agency = db.query(Agency).filter(Agency.code == cfg["agency_code"]).first()
    if not agency:
        agency = Agency(
            code=cfg["agency_code"],
            name=cfg["agency_name"],
            level="local",
            state_id=state_id,
            website_url=cfg["url"].split("/page")[0],
        )
        db.add(agency)
        db.flush()
    return agency


def save_grant(db, data: dict) -> bool:
    """Insert grant if not already present. Returns True if new."""
    if already_exists(db, data["title"], data["source_id"]):
        return False

    opp = Opportunity(
        title=data["title"],
        description=data.get("description"),
        summary=data.get("summary"),
        opportunity_url=data.get("opportunity_url"),
        application_url=data.get("application_url") or "",
        deadline=data.get("deadline"),
        deadline_display=data.get("deadline_display"),
        award_max=data.get("award_max"),
        award_value=data.get("award_value"),
        source_id=data["source_id"],
        agency_id=data.get("agency_id"),
        state_id=data.get("state_id"),
        sponsor_name=data.get("sponsor_name"),
        sponsor_website=data.get("sponsor_website"),
        opportunity_type=OpportunityType.GRANT,
        category=OpportunityCategory.GOVERNMENT,
        status=OpportunityStatus.UNVERIFIED,
        needs_review=True,
        extraction_confidence=0.5,
        data_quality_score=0.5,
        raw_source_data={"scraper": "dc_direct"},
    )
    db.add(opp)
    db.flush()
    db.add(ReviewQueue(opportunity_id=opp.id, priority=1, reason="dc_direct_scraper"))
    return True


# Main pipeline

def run():
    db = SessionLocal()
    total_new = 0

    try:
        dc_state = db.query(State).filter(State.code == "DC").first()
        if not dc_state:
            log.error("DC state not found in DB — run seed_initial_data() first")
            return

        scraper_fn = {"ovsjg": scrape_ovsjg, "doh": scrape_doh}

        for cfg in SOURCES:
            source = get_or_create_source(db, cfg, dc_state.id)
            agency = get_or_create_agency(db, cfg, dc_state.id)

            log_entry = ScrapeLog(source_id=source.id, started_at=datetime.utcnow())
            db.add(log_entry)
            db.flush()

            try:
                grants = scraper_fn[cfg["scraper"]](source.id, agency.id, dc_state.id, db)
                new_count = sum(save_grant(db, g) for g in grants)
                db.commit()

                log_entry.completed_at = datetime.utcnow()
                log_entry.status = "success"
                log_entry.grants_found = len(grants)
                log_entry.grants_new = new_count
                db.commit()

                log.info("%s → %d found, %d new", cfg["name"], len(grants), new_count)
                total_new += new_count

            except Exception as e:
                db.rollback()
                log_entry.status = "error"
                log_entry.error_message = str(e)
                db.commit()
                log.error("%s failed: %s", cfg["name"], e)

        log.info("Done — %d new grants saved", total_new)

    finally:
        db.close()


if __name__ == "__main__":
    run()