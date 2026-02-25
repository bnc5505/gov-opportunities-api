# GrantWatch ETL Pipeline for GovGrants Hub
#
# Reads public GrantWatch DC category pages, searches for the original funder
# page, scrapes full details, and writes everything into PostgreSQL.
#
# Run from project root:
#   cd gov-opportunities-api && python -m app.scrappers.grantwatch_scraper

import os
import sys
import re
import time
import logging
from datetime import datetime, date
from typing import Optional

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser
from urllib.parse import urljoin, urlparse
from sqlalchemy.orm import Session
from ddgs import DDGS

# Allow imports from app/ regardless of working directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from models import (
    Base,
    Opportunity,
    Agency,
    Source,
    State,
    Category,
    ApplicantType,
    ScrapeLog,
    ReviewQueue,
    OpportunityType,
    OpportunityStatus,
    OpportunityCategory,
)
from database import SessionLocal, engine


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("grantwatch_etl")


# Only load grants whose deadline falls strictly after this date.
DEADLINE_CUTOFF = date(2026, 3, 1)

# The 12 GrantWatch DC category pages we scrape.
GRANTWATCH_CATEGORIES = [
    ("https://washingtondc.grantwatch.com/cat/5/community-services-grants.html",                 "Community Services"),
    ("https://washingtondc.grantwatch.com/cat/59/education-grants.html",                         "Education"),
    ("https://washingtondc.grantwatch.com/cat/14/health-and-medical-grants.html",                "Health and Medical"),
    ("https://washingtondc.grantwatch.com/cat/8/community-and-economic-development-grants.html", "Community and Economic Development"),
    ("https://washingtondc.grantwatch.com/cat/13/small-business-grants.html",                    "Small Business"),
    ("https://washingtondc.grantwatch.com/cat/23/mental-health-grants.html",                     "Mental Health"),
    ("https://washingtondc.grantwatch.com/cat/39/women-grants.html",                             "Women"),
    ("https://washingtondc.grantwatch.com/cat/41/youth-and-at-risk-youth-grants.html",           "Youth and At-Risk Youth"),
    ("https://washingtondc.grantwatch.com/cat/7/disabilities-grants.html",                       "Disabilities"),
    ("https://washingtondc.grantwatch.com/cat/19/housing-grants.html",                           "Housing"),
    ("https://washingtondc.grantwatch.com/cat/40/workforce-grants.html",                         "Workforce"),
    ("https://washingtondc.grantwatch.com/cat/10/environment-and-conservation-grants.html",      "Environment and Conservation"),
]

SDG_LIST = [
    ("SDG 1",  "No Poverty",                               ["poverty", "poor", "low income", "financial hardship"]),
    ("SDG 2",  "Zero Hunger",                              ["hunger", "food insecurity", "nutrition", "food access"]),
    ("SDG 3",  "Good Health and Well-being",               ["health", "wellbeing", "medical", "mental health", "healthcare"]),
    ("SDG 4",  "Quality Education",                        ["education", "literacy", "school", "learning", "training"]),
    ("SDG 5",  "Gender Equality",                          ["women", "gender", "female", "girls", "equity"]),
    ("SDG 6",  "Clean Water and Sanitation",               ["water", "sanitation", "clean water", "wastewater"]),
    ("SDG 7",  "Affordable and Clean Energy",              ["energy", "solar", "renewable", "clean energy", "electrification"]),
    ("SDG 8",  "Decent Work and Economic Growth",          ["workforce", "jobs", "employment", "economic growth", "small business"]),
    ("SDG 9",  "Industry, Innovation, and Infrastructure", ["innovation", "infrastructure", "technology", "startup", "incubator"]),
    ("SDG 10", "Reduced Inequality",                       ["inequality", "underserved", "bipoc", "minority", "equity"]),
    ("SDG 11", "Sustainable Cities and Communities",       ["community", "urban", "housing", "sustainable city", "neighborhood"]),
    ("SDG 12", "Responsible Consumption and Production",   ["sustainability", "recycling", "circular economy", "waste reduction"]),
    ("SDG 13", "Climate Action",                           ["climate", "carbon", "emissions", "green", "environment"]),
    ("SDG 14", "Life Below Water",                         ["ocean", "marine", "water quality", "aquatic", "coastal"]),
    ("SDG 15", "Life on Land",                             ["conservation", "biodiversity", "forest", "land", "wildlife"]),
    ("SDG 16", "Peace and Justice Strong Institutions",    ["justice", "peace", "governance", "legal", "civil rights"]),
    ("SDG 17", "Partnerships to Achieve the Goal",         ["partnership", "collaboration", "coalition", "network"]),
]

GAP_RESOURCE_KEYWORDS = {
    "Capital":           ["grant", "funding", "award", "capital", "investment", "loan", "prize"],
    "Networks":          ["network", "community", "peer", "cohort", "connection", "partner"],
    "Capacity Building": ["training", "mentorship", "coaching", "workshop", "technical assistance",
                          "capacity", "accelerator", "bootcamp", "incubator"],
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

REQUEST_DELAY = 2
SEARCH_DELAY  = 3
MAX_RETRIES   = 3


# ── HTTP helpers ─────────────────────────────────────────────────────────────

def safe_get(url: str, retries: int = MAX_RETRIES, delay: float = REQUEST_DELAY) -> Optional[requests.Response]:
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code == 200:
                return resp
            log.warning("HTTP %s for %s (attempt %d)", resp.status_code, url, attempt)
        except requests.RequestException as exc:
            log.warning("Request failed for %s: %s (attempt %d)", url, exc, attempt)
        time.sleep(delay * attempt)
    return None


# ── Database helpers ──────────────────────────────────────────────────────────

def get_or_create_source(db: Session) -> Source:
    source = db.query(Source).filter_by(url="https://washingtondc.grantwatch.com/").first()
    if not source:
        source = Source(
            name="GrantWatch DC",
            url="https://washingtondc.grantwatch.com/",
            scraper_type="grantwatch_web",
            scrape_frequency_hours=24,
            is_active=True,
        )
        db.add(source)
        db.flush()
    return source


def get_dc_state(db: Session) -> Optional[State]:
    return db.query(State).filter_by(code="DC").first()


def get_or_create_agency(db: Session, name: str, website_url: Optional[str],
                          state: Optional[State], level: str = "local") -> Agency:
    code   = re.sub(r"[^A-Z0-9]", "", name.upper())[:50] or "UNKNOWN"
    agency = db.query(Agency).filter_by(code=code).first()
    if not agency:
        agency = Agency(
            code=code,
            name=name[:255],
            website_url=website_url,
            level=level,
            state_id=state.id if state else None,
        )
        db.add(agency)
        db.flush()
    else:
        if website_url and not agency.website_url:
            agency.website_url = website_url
    return agency


def get_or_create_category(db: Session, name: str) -> Category:
    slug     = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    category = db.query(Category).filter_by(slug=slug).first()
    if not category:
        category = Category(name=name, slug=slug, display_order=0, is_active=True)
        db.add(category)
        db.flush()
    return category


def get_or_create_applicant_type(db: Session, name: str, code: str, is_individual: bool) -> ApplicantType:
    at = db.query(ApplicantType).filter_by(code=code).first()
    if not at:
        at = ApplicantType(name=name, code=code, is_individual=is_individual)
        db.add(at)
        db.flush()
    return at


def opportunity_already_exists(db: Session, title: str, deadline: Optional[datetime]) -> bool:
    return db.query(Opportunity).filter_by(title=title, deadline=deadline).first() is not None


# ── Stage 1: Extract ──────────────────────────────────────────────────────────

GRANTWATCH_BASE = "https://washingtondc.grantwatch.com"


def extract_grants_from_category(url: str, category_name: str) -> list:
    log.info("Extracting from: %s", category_name)
    resp = safe_get(url)
    if not resp:
        log.error("Could not fetch %s", url)
        return []

    soup   = BeautifulSoup(resp.text, "lxml")
    grants = []

    # GrantWatch card structure (2025+):
    #   <div class="card-body p-3">
    #     <a class="text-dark text-decoration-none" href="/grant/ID/slug.html">
    #       <h4 class="h6 fw-semibold ...">Title</h4>
    #     </a>
    #     <div class="d-flex mb-2">Deadline: MM/DD/YY</div>
    #     <p class="description_text text-muted ...">Snippet...</p>
    #   </div>
    for card in soup.find_all("div", class_="card-body"):
        h4 = card.find("h4")
        if not h4:
            continue
        title = h4.get_text(strip=True)
        if not title or len(title) < 10:
            continue

        # Grant URL: the <a> wrapping the h4
        parent_a  = h4.find_parent("a")
        grant_url = None
        if parent_a and parent_a.get("href"):
            href = parent_a["href"]
            grant_url = href if href.startswith("http") else GRANTWATCH_BASE + href

        # Deadline: inside the d-flex div
        deadline_raw = ""
        dlflex = card.find("div", class_="d-flex")
        if dlflex:
            text = dlflex.get_text(" ", strip=True)
            dl_match = re.search(r"(\d{2}/\d{2}/\d{2,4}|Ongoing|ongoing)", text, re.IGNORECASE)
            if dl_match:
                deadline_raw = dl_match.group(1).strip()

        # Description snippet
        snippet  = ""
        desc_p   = card.find("p", class_="description_text")
        if desc_p:
            snippet = desc_p.get_text(" ", strip=True)[:800]

        grants.append({
            "title":        title,
            "snippet":      snippet,
            "deadline_raw": deadline_raw,
            "category":     category_name,
            "grant_url":    grant_url,
        })

    log.info("  Found %d grant cards", len(grants))
    return grants


# ── Stage 2: Filter ───────────────────────────────────────────────────────────

def parse_deadline(raw: str) -> Optional[datetime]:
    if not raw or raw.lower() in ("ongoing", ""):
        return None
    try:
        dt = dateparser.parse(raw, dayfirst=False)
        if dt and dt.year < 100:
            dt = dt.replace(year=dt.year + 2000)
        return dt
    except Exception:
        return None


def passes_deadline_filter(deadline: Optional[datetime]) -> bool:
    if deadline is None:
        return False
    return deadline.date() > DEADLINE_CUTOFF


# Keywords that identify a grant as specifically targeting DC / local government.
_DC_TITLE_KEYWORDS = [
    "washington, dc",
    "washington dc",
    "washington, d.c.",
    "district of columbia",
    "dc nonprofits",
    "dc businesses",
    "dc agencies",
    "dc residents",
    "dc-based",
    "dc organizations",
    "d.c. nonprofits",
    "d.c. businesses",
    "d.c. agencies",
]

_DC_SLUG_KEYWORDS = [
    "washington-dc",
    "district-of-columbia",
    "washington-d-c",
]


def is_dc_local_grant(title: str, grant_url: Optional[str] = None) -> bool:
    """Return True only for grants explicitly targeted at Washington, DC / local government."""
    title_lower = title.lower()
    if any(kw in title_lower for kw in _DC_TITLE_KEYWORDS):
        return True
    if grant_url:
        slug = grant_url.lower()
        if any(kw in slug for kw in _DC_SLUG_KEYWORDS):
            return True
    return False


# ── Stage 2b: Scrape GrantWatch detail page ───────────────────────────────────

def get_official_url_and_amount(grant_url: str):
    """
    Fetch the GrantWatch grant detail page and return:
      (official_apply_url, award_min, award_max)

    The official apply URL is the first non-GrantWatch link with
    apply/application/submit/click text.  Award amounts are parsed
    from the full page text.
    """
    resp = safe_get(grant_url)
    if not resp:
        return None, None, None

    soup = BeautifulSoup(resp.text, "lxml")

    # Find the official "Apply" link — must point away from grantwatch.com
    official_url = None
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("http"):
            continue
        if "grantwatch.com" in href.lower():
            continue
        link_text = a.get_text(strip=True).lower()
        if re.search(r"\bapply\b|application|submit|click here|apply online", link_text, re.I):
            official_url = href
            break

    # If no apply-text link found, fall back to any outbound gov/org link
    if not official_url:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not href.startswith("http"):
                continue
            if "grantwatch.com" in href.lower():
                continue
            parsed = urlparse(href)
            if parsed.netloc.endswith((".gov", ".org", ".edu")):
                official_url = href
                break

    # Parse award amounts from the full page text
    full_text = soup.get_text(" ", strip=True)
    award_min, award_max, _ = extract_amounts(full_text)

    return official_url, award_min, award_max


# ── Stage 3: Find primary source ──────────────────────────────────────────────

def search_primary_source(title: str) -> Optional[str]:
    query      = f'"{title}" grant site:dc.gov OR site:*.org OR site:*.gov'
    search_url = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query)}"

    time.sleep(SEARCH_DELAY)
    resp = safe_get(search_url)
    if not resp:
        query      = title + " grant application Washington DC"
        search_url = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query)}"
        time.sleep(SEARCH_DELAY)
        resp = safe_get(search_url)
    if not resp:
        return None

    soup            = BeautifulSoup(resp.text, "lxml")
    blocked_domains = {"grantwatch.com", "bing.com", "google.com", "duckduckgo.com"}

    for result in soup.select(".result__url, .result__a"):
        href = result.get("href", "")
        url_match = re.search(r"uddg=([^&]+)", href)
        if url_match:
            href = requests.utils.unquote(url_match.group(1))
        if not href.startswith("http"):
            href = "https://" + href
        try:
            domain = urlparse(href).netloc.lower().replace("www.", "")
            if not any(blocked in domain for blocked in blocked_domains):
                return href
        except Exception:
            continue
    return None


# ── Stage 4: Scrape primary source ────────────────────────────────────────────

def extract_amounts(text: str):
    award_min  = None
    award_max  = None
    cash_award = None

    range_match = re.search(
        r"\$([\d,]+(?:\.\d+)?)\s*(?:to|-)\s*\$?([\d,]+(?:\.\d+)?)",
        text, re.IGNORECASE
    )
    if range_match:
        award_min = float(range_match.group(1).replace(",", ""))
        award_max = float(range_match.group(2).replace(",", ""))

    up_to = re.search(r"up to\s*\$?([\d,]+(?:\.\d+)?)", text, re.IGNORECASE)
    if up_to and not award_max:
        award_max = float(up_to.group(1).replace(",", ""))

    cash_match = re.search(r"cash\s*(?:prize|award|grant)?\s*(?:of\s*)?\$?([\d,]+(?:\.\d+)?)", text, re.IGNORECASE)
    if cash_match:
        cash_award = float(cash_match.group(1).replace(",", ""))

    if not award_max:
        amounts = []
        for m in re.finditer(r"\$([\d,]+(?:\.\d+)?)", text):
            try:
                amounts.append(float(m.group(1).replace(",", "")))
            except Exception:
                pass
        amounts = sorted(set(amounts))
        if len(amounts) >= 2:
            award_min, award_max = amounts[0], amounts[-1]
        elif len(amounts) == 1:
            award_max = amounts[0]

    return award_min, award_max, cash_award


def format_award_value(award_min, award_max) -> str:
    if award_min and award_max:
        return f"${award_min:,.0f} - ${award_max:,.0f}"
    if award_max:
        return f"${award_max:,.0f}"
    if award_min:
        return f"${award_min:,.0f}"
    return "Not specified"


def format_deadline_display(deadline: Optional[datetime]) -> str:
    if not deadline:
        return "Ongoing"
    return deadline.strftime("%b %d, %Y")


def detect_sdg_alignment(text: str) -> list:
    matched    = []
    text_lower = text.lower()
    for sdg_num, sdg_name, keywords in SDG_LIST:
        if any(kw in text_lower for kw in keywords):
            matched.append(f"{sdg_num}: {sdg_name}")
    return matched


def detect_opportunity_gap_resources(text: str) -> list:
    detected   = []
    text_lower = text.lower()
    for resource, keywords in GAP_RESOURCE_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            detected.append(resource)
    return detected or ["Capital"]


def detect_tags(title: str, description: str, category_name: str) -> list:
    tags        = [category_name]
    combined    = (title + " " + description).lower()
    keyword_map = {
        "nonprofit":      "Nonprofit",
        "small business": "Small Business",
        "women":          "Women-Owned",
        "youth":          "Youth",
        "disability":     "Disabilities",
        "mental health":  "Mental Health",
        "housing":        "Housing",
        "workforce":      "Workforce Development",
        "environment":    "Environment",
        "education":      "Education",
        "health":         "Healthcare",
        "community":      "Community Development",
        "economic":       "Economic Development",
        "technology":     "Technology",
        "veteran":        "Veterans",
        "immigrant":      "Immigrants",
        "bipoc":          "BIPOC",
        "equity":         "Equity",
        "climate":        "Climate",
        "energy":         "Energy",
        "capital":        "Capital Access",
        "entrepreneur":   "Entrepreneurship",
        "innovation":     "Innovation",
    }
    for keyword, tag in keyword_map.items():
        if keyword in combined and tag not in tags:
            tags.append(tag)
    return tags[:12]


def detect_eligibility_flags(text: str):
    t            = text.lower()
    individual   = any(w in t for w in ["individual", "person", "researcher", "artist", "student"])
    organization = any(w in t for w in ["nonprofit", "organization", "agency", "business",
                                         "institution", "municipality", "school", "company"])
    if not individual and not organization:
        organization = True
    return individual, organization


def extract_eligibility_requirements(text: str) -> list:
    requirements = []
    elig_match = re.search(
        r"(who can apply|eligib|qualif)(.*?)(\n\n|\Z)",
        text, re.IGNORECASE | re.DOTALL
    )
    if elig_match:
        block = elig_match.group(2)
        items = re.split(r"[\n\r]+|\d+\.\s+", block)
        for item in items:
            item = item.strip().strip("-").strip()
            if len(item) > 15:
                requirements.append(item[:300])
                if len(requirements) >= 8:
                    break
    if not requirements:
        sentences = re.split(r"[.!?]", text)
        for s in sentences:
            if re.search(r"(must be|eligible|qualif|applicant)", s, re.IGNORECASE):
                s = s.strip()
                if len(s) > 15:
                    requirements.append(s[:300])
                    if len(requirements) >= 5:
                        break
    return requirements


def detect_disqualifying_flags(text: str) -> dict:
    text_lower = text.lower()
    result = {
        "fee_required":        False,
        "fee_amount":          None,
        "cost_to_participate": False,
        "cost_amount":         None,
        "equity_percentage":   False,
        "equity_details":      None,
        "safe_note":           False,
        "safe_note_details":   None,
    }

    fee_match = re.search(r"(application fee|registration fee|entry fee)[^\$]*\$?([\d,]+)", text_lower)
    if fee_match:
        result["fee_required"] = True
        result["fee_amount"]   = f"${fee_match.group(2)}"

    cost_match = re.search(r"(cost to participate|participation fee|program fee)[^\$]*\$?([\d,]+)", text_lower)
    if cost_match:
        result["cost_to_participate"] = True
        result["cost_amount"]         = f"${cost_match.group(2)}"

    equity_match = re.search(r"equity|ownership stake|percentage of (company|business)", text_lower)
    if equity_match:
        result["equity_percentage"] = True
        pct_match = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
        result["equity_details"] = f"{pct_match.group(0)} equity" if pct_match else "Equity mentioned in description"

    if "safe note" in text_lower or "simple agreement for future equity" in text_lower:
        result["safe_note"]         = True
        result["safe_note_details"] = "SAFE note mentioned in description"

    return result


def detect_is_global(text: str, title: str):
    global_keywords = ["global", "international", "worldwide", "all countries",
                        "any country", "outside the us", "outside the united states"]
    text_lower = (title + " " + text).lower()
    is_global  = any(kw in text_lower for kw in global_keywords)
    region_keywords = ["europe", "africa", "asia", "latin america", "middle east",
                       "west africa", "east africa", "south america", "caribbean",
                       "canada", "mexico", "united kingdom", "france", "germany"]
    locations = [r.title() for r in region_keywords if r in text_lower]
    return is_global, locations


def detect_rolling(text: str, deadline: Optional[datetime]) -> bool:
    if deadline is None:
        return True
    rolling_keywords = ["rolling basis", "rolling deadline", "rolling applications",
                        "accepted on a rolling", "no deadline", "open until filled"]
    return any(kw in text.lower() for kw in rolling_keywords)


def extract_contact_names(text: str) -> Optional[str]:
    name_match = re.search(
        r"(?:contact|reach out to|contact person)[:\s]+([A-Z][a-z]+\s+[A-Z][a-z]+)",
        text
    )
    return name_match.group(1) if name_match else None


def infer_agency_level(title: str) -> str:
    state_keywords = ["Maryland", "Virginia", "West Virginia", "Pennsylvania",
                      "Delaware", "North Carolina", "Georgia", "Florida"]
    for kw in state_keywords:
        if kw.lower() in title.lower():
            return "state"
    return "local"


def extract_logo_url(soup: BeautifulSoup, base_url: str) -> Optional[str]:
    for img in soup.find_all("img"):
        src       = img.get("src", "")
        alt       = img.get("alt", "").lower()
        css_class = " ".join(img.get("class", [])).lower()
        img_id    = img.get("id", "").lower()
        if any(kw in css_class + img_id + alt for kw in ["logo", "brand", "header"]):
            full_src = urljoin(base_url, src)
            if full_src.startswith("http"):
                return full_src

    favicon_link = soup.find("link", rel=lambda r: r and "icon" in " ".join(r).lower())
    if favicon_link:
        href = favicon_link.get("href", "")
        if href:
            return urljoin(base_url, href)

    parsed  = urlparse(base_url)
    favicon = f"{parsed.scheme}://{parsed.netloc}/favicon.ico"
    check   = safe_get(favicon)
    if check:
        return favicon

    return None


def build_summary(description: str, title: str) -> str:
    if not description:
        return f"{title} - see opportunity details for more information."
    sentences  = re.split(r"(?<=[.!?])\s+", description.strip())
    meaningful = [s for s in sentences if len(s) > 30][:2]
    if meaningful:
        return " ".join(meaningful)[:500]
    return description[:497] + "..." if len(description) > 500 else description


def scrape_primary_source(url: str) -> dict:
    result = {
        "description":              None,
        "summary":                  None,
        "award_min":                None,
        "award_max":                None,
        "cash_award":               None,
        "eligibility_requirements": [],
        "eligibility_individual":   False,
        "eligibility_organization": False,
        "application_url":          url,
        "opportunity_url":          url,
        "agency_name":              None,
        "agency_website":           None,
        "sponsor_name":             None,
        "sponsor_website":          None,
        "logo_url":                 None,
        "contact_names":            None,
        "contact_email":            None,
        "contact_phone":            None,
        "tags":                     [],
        "sdg_alignment":            [],
        "opportunity_gap_resources": [],
        "is_global":                False,
        "location":                 [],
        "rolling":                  False,
        "fee_required":             False,
        "fee_amount":               None,
        "cost_to_participate":      False,
        "cost_amount":              None,
        "equity_percentage":        False,
        "equity_details":           None,
        "safe_note":                False,
        "safe_note_details":        None,
        "raw_source_data":          {"source_url": url},
        "extraction_confidence":    0.5,
    }

    resp = safe_get(url)
    if not resp:
        result["extraction_confidence"] = 0.1
        return result

    soup = BeautifulSoup(resp.text, "lxml")

    h1         = soup.find("h1")
    page_title = soup.find("title")
    if h1:
        result["agency_name"]   = h1.get_text(strip=True)[:255]
        result["sponsor_name"]  = h1.get_text(strip=True)[:255]
    elif page_title:
        result["agency_name"]   = page_title.get_text(strip=True)[:255]
        result["sponsor_name"]  = page_title.get_text(strip=True)[:255]
    base_domain               = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    result["agency_website"]  = base_domain
    result["sponsor_website"] = base_domain

    result["logo_url"] = extract_logo_url(soup, url)

    content_el = (
        soup.find("article")
        or soup.find("main")
        or soup.find("div", {"id": re.compile(r"content|main", re.I)})
        or soup.find("div", {"class": re.compile(r"content|main|body", re.I)})
    )
    if content_el:
        for tag in content_el.find_all(["nav", "footer", "header", "script", "style"]):
            tag.decompose()
        full_text = content_el.get_text(" ", strip=True)
    else:
        full_text = soup.get_text(" ", strip=True)

    result["description"]    = full_text[:5000] if full_text else None
    result["raw_source_data"]["full_text_sample"] = full_text[:1000] if full_text else ""

    award_min, award_max, cash_award = extract_amounts(full_text or "")
    result["award_min"]  = award_min
    result["award_max"]  = award_max
    result["cash_award"] = cash_award

    result["eligibility_requirements"] = extract_eligibility_requirements(full_text or "")
    ind, org = detect_eligibility_flags(full_text or "")
    result["eligibility_individual"]   = ind
    result["eligibility_organization"] = org

    for a in soup.find_all("a", href=True):
        txt  = a.get_text(strip=True).lower()
        href = a["href"]
        if re.search(r"apply|application|rfa|submit|click here", txt, re.I):
            full_href = urljoin(url, href)
            if full_href.startswith("http"):
                result["application_url"] = full_href
                break

    result["contact_names"] = extract_contact_names(full_text or "")
    email_match = re.search(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", full_text or "")
    if email_match:
        result["contact_email"] = email_match.group(0)[:255]
    phone_match = re.search(r"\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}", full_text or "")
    if phone_match:
        result["contact_phone"] = phone_match.group(0)[:50]

    result["sdg_alignment"]            = detect_sdg_alignment(full_text or "")
    result["opportunity_gap_resources"] = detect_opportunity_gap_resources(full_text or "")

    is_global, locations = detect_is_global(full_text or "", "")
    result["is_global"] = is_global
    result["location"]  = locations
    result["rolling"]   = detect_rolling(full_text or "", None)

    flags = detect_disqualifying_flags(full_text or "")
    result.update(flags)

    score = 0.4
    if result["description"]:               score += 0.15
    if result["award_max"]:                 score += 0.1
    if result["eligibility_requirements"]:  score += 0.1
    if result["application_url"] != url:    score += 0.1
    if result["contact_email"]:             score += 0.05
    if result["sdg_alignment"]:             score += 0.05
    if result["logo_url"]:                  score += 0.05
    result["extraction_confidence"] = round(min(score, 1.0), 2)

    return result


# ── Stage 4: Web search for official sponsor page ────────────────────────────

# Domains to skip — aggregators, search engines, or off-topic sites.
_SKIP_DOMAINS = {
    "grantwatch.com", "grants.gov", "bing.com", "google.com",
    "duckduckgo.com", "facebook.com", "twitter.com", "linkedin.com",
    "youtube.com", "wikipedia.org",
    # Grant aggregators / info sites — not the original source
    "grantedai.com", "candid.org", "guidestar.org", "charitynavigator.org",
    "fundsnetservices.com", "philanthropy.com", "foundationcenter.org",
    "grantspace.org", "instrumentl.com", "cause.org", "grantforward.com",
    "grantstation.com", "submittable.com", "fluxx.io",
    "federalgrantswire.com", "grantslist.org", "grantsforgood.net",
    # Education extension / info pages (not DC grants)
    "extension.umd.edu", "extension.psu.edu",
    # Generic info / content sites
    "environmentalscience.org", "lacommunityalliance.org",
    "incubatorlist.com", "nwejc.org",
    "esd.wa.gov",   # Washington *State* — not Washington DC
    "ssa.gov",      # Social Security Admin — not a grant funder
    "midcitydcnews.com",  # local news — not a grant source
    "washington.jl.org",  # Junior League — blocks scrapers
}

_SNIPPET_STOP_WORDS = {
    "the", "a", "an", "to", "for", "of", "in", "and", "or", "is", "are",
    "was", "were", "be", "been", "have", "has", "had", "will", "would",
    "may", "might", "can", "could", "that", "this", "with", "from", "at",
    "by", "on", "as", "its", "it", "their", "them", "they", "who", "which",
    "grant", "grants", "funding", "funded", "fund", "funds",
    "washington", "nonprofits", "nonprofit", "organizations", "organization",
    "programs", "program", "project", "projects", "eligible", "eligibility",
    "intended", "available", "focus", "focused", "areas", "area",
    "support", "supports", "provide", "provides", "include", "including",
    "residents", "communities", "community", "local", "located", "location",
    "within", "based", "based", "applicants", "applicant",
}


def extract_sponsor_from_snippet(snippet: str, title: str = "") -> Optional[str]:
    """
    Try to pull the funder/agency name from the public GrantWatch listing snippet.

    GrantWatch snippets often start with the funder:
      "The DC Office of the Attorney General is offering grants..."
      "The Metropolitan Police Department provides funding to..."
      "DC Health awards grants to..."
      "Applications are invited by the Department of Housing..."

    Strategy: greedy patterns that capture multi-word names, then stop at a
    clause-boundary (period, comma, "to verb", end of string).
    """
    combined = (snippet + " " + title).strip()

    # Verb anchors — words that signal the END of an agency name
    # (?!\w) prevents "was" from matching the start of "Washington", etc.
    _VERBS   = r"(?:is|are|was|were|awards?|provides?|funds?|supports?|offers?|administers?|announces?|invites?|will\s+\w+)(?!\w)"
    # "to [lowercase-word]" or "for [lowercase-word/article]" signal end of agency name
    # Agency names always capitalise their key words; lowercase after "to/for" = clause
    _PREP_TO = r"to\s+[a-z]"
    _PREP_FOR= r"for\s+(?:the\s+\w|a\s+\w|[a-z])"
    _TERM_LA = rf"(?=\s+(?:{_VERBS}|{_PREP_TO}|{_PREP_FOR})|[,.]|$)"

    patterns = [
        # "The DC Office of ... is/are offering/awarding/providing"  — lookahead at verb
        rf"^(?:The\s+)?([A-Z][A-Za-z &,'.\-]{{4,80}}?)(?=\s+(?:is|are)\s+(?:offering|awarding|providing|accepting|seeking|inviting|announcing|distributing))",
        # "The [Name] awards/provides/funds" — lookahead at verb
        rf"^(?:The\s+)?([A-Z][A-Za-z &,'.\-]{{4,80}}?)(?=\s+{_VERBS})",
        # "Applications are invited by the [Name]" — greedy then term lookahead
        rf"(?:applications?\s+(?:are\s+)?(?:invited|accepted|reviewed)\s+by\s+(?:the\s+)?)([A-Z][A-Za-z &,'.\-]{{4,80}}?){_TERM_LA}",
        # "offered/funded/provided by [Name]" — greedy then term lookahead
        rf"(?:offered|funded|provided|administered|managed|sponsored)\s+by\s+(?:the\s+)?([A-Z][A-Za-z &,'.\-]{{4,80}}?){_TERM_LA}",
        # "from/through the [Name] to support/for..."  — greedy then prep lookahead
        rf"(?:from|through)\s+(?:the\s+)?([A-Z][A-Za-z &,'.\-]{{4,80}}?)(?=\s+(?:{_PREP_TO}|{_PREP_FOR}))",
        # DC-prefixed agencies: "DC Health", "DC Housing Authority" + verb  — lookahead
        rf"\b(DC\s+[A-Z][A-Za-z &,'.\-]{{3,60}}?)(?=\s+{_VERBS})",
    ]

    _TOO_GENERIC = {"Washington", "DC", "Grants", "Grant", "Funding", "Program",
                    "Applications", "Applicants", "Organizations", "Nonprofits",
                    "Residents", "Businesses", "Individuals", "Community"}

    for pattern in patterns:
        m = re.search(pattern, combined, re.IGNORECASE)
        if m:
            name = m.group(1).strip().rstrip(".,;: \t")
            # Trim trailing prepositions / articles that leaked in
            name = re.sub(r"\s+(of|the|for|in|to|at|and|or|a|an)$", "", name, flags=re.IGNORECASE).strip()
            words = name.split()
            if not words:
                continue
            # Reject single-word generics or all-generic multi-word names
            if len(words) == 1 and words[0].lower() in {w.lower() for w in _TOO_GENERIC}:
                continue
            if len(words) >= 2 and all(w.lower() in {g.lower() for g in _TOO_GENERIC} for w in words):
                continue
            if 4 < len(name) < 120:
                return name
    return None


def _build_search_query(title: str, snippet: str,
                        grant_url: Optional[str] = None,
                        award_max: Optional[float] = None) -> str:
    """
    Build a targeted search query that can identify the specific DC grant.

    The award amount + specific snippet words make the query unique enough
    to find the actual sponsor page rather than generic grant aggregators.
    """
    # Award amount string for anchoring the search
    amount_str = ""
    if award_max:
        amount_str = f"${award_max:,.0f}".replace(",000", "k")  # e.g. "$15k"

    # Slug-derived topic (most reliable: GrantWatch never truncates slugs)
    slug_topic = ""
    if grant_url:
        slug = grant_url.rstrip("/").split("/")[-1].replace(".html", "")
        slug_words = slug.replace("-", " ").lower()
        for_m = re.search(r"\bfor\s+(.+)$", slug_words)
        if for_m:
            slug_topic = for_m.group(1).strip()

    # Pull the most informative words from the snippet
    # (these are more unique than the standardised title phrasing)
    snippet_tokens = []
    for w in snippet.split():
        clean = re.sub(r"[^a-zA-Z0-9$]", "", w).lower()
        if len(clean) >= 5 and clean not in _SNIPPET_STOP_WORDS:
            snippet_tokens.append(clean)
    unique_snippet_words = list(dict.fromkeys(snippet_tokens))[:5]

    # Identity/demographic keywords are highly discriminating
    identity_kws = re.findall(
        r"\b(BIPOC|LGBTQ\+?|Veteran[s]?|Immigrant[s]?|Refugee[s]?|"
        r"Women|Youth|Senior[s]?|Native\s+American|Indigenous|Asian|"
        r"African\s+American|Latino|Latina|Hispanic)\b",
        title + " " + slug_topic, re.IGNORECASE,
    )
    identity_prefix = " ".join(dict.fromkeys(identity_kws))

    # Prefer slug topic; supplement with snippet words
    topic_words = (slug_topic or " ".join(unique_snippet_words)).split()[:6]
    topic = " ".join(topic_words)

    parts = [p for p in [identity_prefix, topic, amount_str] if p]
    query = " ".join(parts).strip()
    query = " ".join(query.split()[:12])
    query = f"{query} Washington DC grant 2026"
    return query.strip()


def _amount_variants(award_max: float) -> list:
    """Return all plausible textual forms of a dollar amount."""
    variants = []
    # 15000 → ["15,000", "15000", "$15,000", "$15k", "15 000"]
    variants.append(f"{award_max:,.0f}")           # "15,000"
    variants.append(f"{int(award_max)}")           # "15000"
    if award_max >= 1000 and award_max % 1000 == 0:
        k = int(award_max // 1000)
        variants.append(f"${k}k")                 # "$15k"
        variants.append(f"${k}K")
        variants.append(f"{k},000")               # "15,000" (already there but OK)
    if award_max >= 1000000 and award_max % 1000000 == 0:
        m = int(award_max // 1000000)
        variants.append(f"${m}M")
        variants.append(f"${m} million")
        variants.append(f"{m} million")
    return variants


def _validate_result(url: str, award_max: Optional[float],
                     topic_words: Optional[list] = None,
                     authoritative: bool = False) -> bool:
    """
    Return True only if the page looks like it describes THIS specific grant.

    Requirements:
      1. Page returns 200 on first try (no retries — 403/404 means wrong page)
      2. Page text contains the grant dollar amount in ANY common format
         (or domain is authoritative .gov/.org and topic words match — relaxed)
      3. Page text contains at least 3 of the grant's unique topic words
         (extracted from the GrantWatch URL slug, min 5-char words)

    authoritative=True (dc.gov / .org official sites): relax amount check so that
    a strong topic-word match is enough even if the dollar amount format differs.
    """
    # Single-shot fetch — don't retry (saves minutes on 403 pages)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=8)
    except Exception:
        return False
    if resp.status_code != 200:
        return False

    text       = resp.text
    text_lower = text.lower()

    # ── Check 1: dollar amount (any recognisable format) ─────────────────────
    amount_found = False
    if award_max:
        for variant in _amount_variants(award_max):
            if variant in text:
                amount_found = True
                break
        if not amount_found and not authoritative:
            return False   # strict: amount must be present

    # ── Check 2: at least 3 topic words (minimum 5 chars) ────────────────────
    topic_ok = True
    if topic_words:
        long_words  = [w for w in topic_words if len(w) >= 5]
        if long_words:
            # For authoritative domains require more topic words when amount absent
            min_matches = max(3, len(long_words) // 2)
            if authoritative and not amount_found:
                min_matches = max(4, (len(long_words) * 2) // 3)
            matches = sum(1 for w in long_words if w.lower() in text_lower)
            if matches < min_matches:
                topic_ok = False

    if not topic_ok:
        return False

    # For authoritative domain with no amount: must have strong topic match
    if award_max and not amount_found and authoritative:
        # Already required more topic words above — allow it through
        pass

    return True


def _slug_topic_words(grant_url: Optional[str]) -> list:
    """Extract unique topic words from the GrantWatch URL slug."""
    if not grant_url:
        return []
    slug = grant_url.rstrip("/").split("/")[-1].replace(".html", "")
    words = slug.replace("-", " ").lower().split()
    stop  = _SNIPPET_STOP_WORDS | {
        "grants", "grant", "washington", "nonprofits", "nonprofit",
        "organizations", "organization", "agencies", "agency",
        "schools", "school", "businesses", "business",
        "institutions", "institution", "groups", "group",
        "maryland", "virginia", "carolina", "north", "south",
        "ihes", "cbos", "faith", "based", "start", "ups",
        "enterprises", "enterprise", "certified",
    }
    return [w for w in words if len(w) >= 4 and w not in stop]


def _ddg_search(query: str, max_results: int = 10) -> list:
    """Run a DDG text search with up to 2 retries."""
    for attempt in range(1, 3):
        try:
            with DDGS() as ddgs:
                return list(ddgs.text(query, max_results=max_results))
        except Exception as exc:
            log.warning("  DDG attempt %d failed: %s", attempt, exc)
            if attempt < 2:
                time.sleep(4)
    return []


def _classify_results(results: list) -> tuple:
    """Sort DDG results into dc_gov / gov / org / other buckets."""
    dc_gov_hits, gov_hits, org_hits, other_hits = [], [], [], []
    for r in results:
        url    = r.get("href", "")
        if not url.startswith("http"):
            continue
        domain = urlparse(url).netloc.lower().replace("www.", "")
        if any(skip in domain for skip in _SKIP_DOMAINS):
            continue
        if domain.endswith(".gov"):
            if "dc.gov" in domain or domain.startswith("dc."):
                dc_gov_hits.append(url)
            else:
                gov_hits.append(url)
        elif domain.endswith(".org") or domain.endswith(".edu"):
            org_hits.append(url)
        else:
            other_hits.append(url)
    return dc_gov_hits, gov_hits, org_hits, other_hits


def _is_authoritative(url: str) -> bool:
    """True if the URL is from a .gov or .org domain (trusted enough for relaxed validation)."""
    domain = urlparse(url).netloc.lower().replace("www.", "")
    return domain.endswith(".gov") or domain.endswith(".org") or domain.endswith(".edu")


def search_official_grant_page(title: str, snippet: str,
                               grant_url: Optional[str] = None,
                               award_max: Optional[float] = None) -> Optional[str]:
    """
    Search for the official sponsor page of this DC grant.

    Phase 0 — Sponsor-targeted: extract sponsor/agency from snippet → search
              '"{sponsor}" grant site:dc.gov' and '"{sponsor}" grant application DC'
    Phase 1 — site:dc.gov {slug_topic} grant
    Phase 2 — Broad search with identity + topic + amount keywords

    Validation:
      • strict (default): page must return 200 + contain dollar amount + ≥3 topic words
      • authoritative (.gov/.org): relax amount requirement if topic words strongly match
    """
    topic_words = _slug_topic_words(grant_url)
    slug_topic  = " ".join(topic_words[:6]) if topic_words else ""

    # ── Phase 0: Sponsor / agency targeted search ────────────────────────────
    sponsor = extract_sponsor_from_snippet(snippet, title)
    if sponsor:
        log.info("  Sponsor extracted: %s", sponsor)

        # 0a — sponsor site:dc.gov
        q0a = f'"{sponsor}" grant site:dc.gov'
        log.info("  Phase 0a search: %s", q0a)
        r0a = _ddg_search(q0a, max_results=5)
        dc0, _, _, _ = _classify_results(r0a)
        for url in dc0[:3]:
            auth = _is_authoritative(url)
            if _validate_result(url, award_max, topic_words, authoritative=auth):
                log.info("  [Phase 0a] Validated: %s", url)
                return url

        # 0b — sponsor broad DC grant search
        q0b = f'"{sponsor}" grant application Washington DC 2026'
        log.info("  Phase 0b search: %s", q0b)
        r0b = _ddg_search(q0b, max_results=8)
        dc_b, gov_b, org_b, other_b = _classify_results(r0b)
        attempts = 0
        for candidate_list in (dc_b, gov_b, org_b, other_b):
            for url in candidate_list:
                if attempts >= 4:
                    break
                attempts += 1
                auth = _is_authoritative(url)
                if _validate_result(url, award_max, topic_words, authoritative=auth):
                    log.info("  [Phase 0b] Validated: %s", url)
                    return url

    # ── Phase 1: DC government sites via slug topic ──────────────────────────
    dc_query = f"site:dc.gov {slug_topic} grant"
    log.info("  Phase 1 search: %s", dc_query)
    dc_results  = _ddg_search(dc_query, max_results=5)
    dc_gov_hits, _, _, _ = _classify_results(dc_results)

    for url in dc_gov_hits[:3]:
        if _validate_result(url, award_max, topic_words, authoritative=True):
            log.info("  [Phase 1] Validated dc.gov page: %s", url)
            return url

    # ── Phase 2: Broad search ─────────────────────────────────────────────────
    broad_query = _build_search_query(title, snippet, grant_url, award_max)
    log.info("  Phase 2 search: %s", broad_query)
    broad_results = _ddg_search(broad_query, max_results=10)
    dc_gov_hits2, gov_hits, org_hits, other_hits = _classify_results(broad_results)

    attempts = 0
    for candidate_list in (dc_gov_hits2, gov_hits, org_hits, other_hits):
        for url in candidate_list:
            if attempts >= 5:
                break
            attempts += 1
            auth = _is_authoritative(url)
            if _validate_result(url, award_max, topic_words, authoritative=auth):
                log.info("  [Phase 2] Validated page: %s", url)
                return url

    log.info("  No validated official page — will use GrantWatch URL")
    return None


def scrape_sponsor_info(url: str) -> dict:
    """
    Fetch the official grant page and extract:
      sponsor_name, sponsor_website, application_url,
      description (richer), eligibility_requirements,
      contact info, logo_url
    """
    empty = {
        "sponsor_name": None, "sponsor_website": None,
        "application_url": url, "opportunity_url": url,
        "description": None, "eligibility_requirements": [],
        "contact_names": None, "contact_email": None,
        "contact_phone": None, "logo_url": None,
        "confidence_bonus": 0.0,
    }

    resp = safe_get(url)
    if not resp:
        return empty

    soup = BeautifulSoup(resp.text, "lxml")

    # Sponsor name: prefer <h1> / page <title>
    h1 = soup.find("h1")
    page_title_el = soup.find("title")
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    sponsor_name = None
    if h1:
        sponsor_name = h1.get_text(strip=True)[:255]
    elif page_title_el:
        # Strip "| Agency Name" suffix patterns from page titles
        raw = page_title_el.get_text(strip=True)
        parts = re.split(r"\s*[|–—-]\s*", raw)
        sponsor_name = parts[-1].strip()[:255] if len(parts) > 1 else raw[:255]

    # Look for an "Apply" link pointing to a form / application portal
    apply_url = None
    for a in soup.find_all("a", href=True):
        txt  = a.get_text(strip=True).lower()
        href = a["href"]
        if re.search(r"\bapply\b|application|submit|apply\s+now|apply\s+online|apply\s+here", txt, re.I):
            full = urljoin(url, href)
            if full.startswith("http"):
                apply_url = full
                break

    # Try to extract a richer description from the page content
    content_el = (
        soup.find("article")
        or soup.find("main")
        or soup.find("div", {"id": re.compile(r"content|main", re.I)})
        or soup.find("div", {"class": re.compile(r"content|main|body", re.I)})
    )
    if content_el:
        for tag in content_el.find_all(["nav", "footer", "header", "script", "style"]):
            tag.decompose()
        full_text = content_el.get_text(" ", strip=True)
    else:
        full_text = soup.get_text(" ", strip=True)

    description    = full_text[:3000] if full_text else None
    eligibility    = extract_eligibility_requirements(full_text or "")
    contact_names  = extract_contact_names(full_text or "")
    email_match    = re.search(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", full_text or "")
    phone_match    = re.search(r"\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}", full_text or "")
    logo_url       = extract_logo_url(soup, url)

    # Confidence bonus: more data = higher bonus
    bonus = 0.2  # already on official page
    if apply_url and apply_url != url: bonus += 0.1
    if eligibility:                    bonus += 0.1
    if email_match:                    bonus += 0.05
    if logo_url:                       bonus += 0.05

    return {
        "sponsor_name":             sponsor_name,
        "sponsor_website":          base_url,
        "application_url":          apply_url or url,
        "opportunity_url":          url,
        "description":              description,
        "eligibility_requirements": eligibility,
        "contact_names":            contact_names,
        "contact_email":            email_match.group(0)[:255] if email_match else None,
        "contact_phone":            phone_match.group(0)[:50]  if phone_match else None,
        "logo_url":                 logo_url,
        "confidence_bonus":         bonus,
    }


# ── Stage 5 & 6: Transform and Load ──────────────────────────────────────────

def run_pipeline():
    log.info("GovGrants Hub - GrantWatch ETL Pipeline starting")
    log.info("Loading grants with deadlines after %s", DEADLINE_CUTOFF.strftime("%B %d, %Y"))

    # Ensure all tables exist before querying (needed when running standalone, outside FastAPI)
    Base.metadata.create_all(bind=engine)

    db             = SessionLocal()
    scrape_started = datetime.utcnow()
    source         = None

    stats = {"found": 0, "filtered_out": 0, "new": 0, "skipped_duplicate": 0, "error": 0}

    try:
        source   = get_or_create_source(db)
        dc_state = get_dc_state(db)
        db.commit()

        at_nonprofit  = get_or_create_applicant_type(db, "Nonprofit Organization", "nonprofit",  False)
        at_business   = get_or_create_applicant_type(db, "Small Business",         "business",   False)
        at_individual = get_or_create_applicant_type(db, "Individual",              "individual", True)
        db.commit()

        for category_url, category_name in GRANTWATCH_CATEGORIES:

            raw_grants = extract_grants_from_category(category_url, category_name)
            stats["found"] += len(raw_grants)
            time.sleep(REQUEST_DELAY)

            category = get_or_create_category(db, category_name)
            db.commit()

            for raw in raw_grants:
                title        = raw["title"]
                snippet      = raw["snippet"]
                deadline_raw = raw["deadline_raw"]
                grant_url    = raw.get("grant_url")

                # ── Filter 1: deadline ────────────────────────────────────────
                deadline = parse_deadline(deadline_raw)
                if not passes_deadline_filter(deadline):
                    log.debug("Filtered (deadline %s): %s", deadline_raw or "none", title[:60])
                    stats["filtered_out"] += 1
                    continue

                # ── Filter 2: DC / local government only ─────────────────────
                if not is_dc_local_grant(title, grant_url):
                    log.debug("Filtered (not DC local): %s", title[:70])
                    stats["filtered_out"] += 1
                    continue

                log.info("DC grant passes date filter - %s - %s", deadline.strftime("%Y-%m-%d"), title[:70])

                # ── Dedup check ───────────────────────────────────────────────
                if opportunity_already_exists(db, title, deadline):
                    log.info("  Already in database, skipping.")
                    stats["skipped_duplicate"] += 1
                    continue

                # ── Enrich from title + snippet ───────────────────────────────
                # NOTE: GrantWatch detail pages are behind a subscription wall.
                # Award amounts and official apply URLs are only extracted from
                # the public listing snippet.  We do not scrape detail pages.
                combined = title + " " + snippet
                award_min_v, award_max_v, cash_v = extract_amounts(combined)

                # ── Filter 3: award amount must be stated in the listing ───────
                if award_min_v is None and award_max_v is None:
                    log.info("  Filtered (no award amount in snippet): %s", title[:70])
                    stats["filtered_out"] += 1
                    continue

                sdg_v       = detect_sdg_alignment(combined)
                gap_v       = detect_opportunity_gap_resources(combined)
                is_global_v = any(w in combined.lower() for w in ["international", "canada", "global", "worldwide"])
                is_indiv_v  = any(w in combined.lower() for w in ["individual", "student", "researcher", "artist", "journalist"])

                # ── Stage 4: Search web for official sponsor page ─────────────
                # Also extract the sponsor name from the snippet now so we can
                # use it even when the official page search fails.
                snippet_sponsor = extract_sponsor_from_snippet(snippet, title)

                official_page_url = search_official_grant_page(
                    title, snippet, grant_url, award_max=award_max_v
                )
                time.sleep(2)  # polite delay after search

                sponsor_info = {}
                if official_page_url:
                    sponsor_info = scrape_sponsor_info(official_page_url)
                    log.info(
                        "  Sponsor: %s  apply_url: %s",
                        str(sponsor_info.get("sponsor_name") or "?")[:60],
                        str(sponsor_info.get("application_url") or "?")[:80],
                    )
                elif snippet_sponsor:
                    # We know the sponsor name even without an official page
                    log.info("  Sponsor (from snippet, no official page): %s", snippet_sponsor[:60])

                # Merge: sponsor info overrides listing defaults
                confidence_v = 0.5 + sponsor_info.get("confidence_bonus", 0.0)

                # Fall back to snippet-derived sponsor when official page not found
                final_sponsor_name    = sponsor_info.get("sponsor_name") or snippet_sponsor
                final_sponsor_website = sponsor_info.get("sponsor_website")

                enriched = {
                    "description":     sponsor_info.get("description") or snippet or None,
                    "summary":         None,
                    "award_min":       award_min_v,
                    "award_max":       award_max_v,
                    "cash_award":      cash_v,
                    "eligibility_requirements": sponsor_info.get("eligibility_requirements") or [],
                    "eligibility_individual":   is_indiv_v,
                    "eligibility_organization": True,
                    # Prefer official apply URL → official page URL → GrantWatch URL
                    "application_url": (
                        sponsor_info.get("application_url")
                        or official_page_url
                        or grant_url
                    ),
                    "opportunity_url": official_page_url or grant_url,
                    "agency_name":     final_sponsor_name,
                    "agency_website":  final_sponsor_website,
                    "sponsor_name":    final_sponsor_name,
                    "sponsor_website": final_sponsor_website,
                    "logo_url":        sponsor_info.get("logo_url"),
                    "contact_names":   sponsor_info.get("contact_names"),
                    "contact_email":   sponsor_info.get("contact_email"),
                    "contact_phone":   sponsor_info.get("contact_phone"),
                    "tags": [category_name], "sdg_alignment": sdg_v,
                    "opportunity_gap_resources": gap_v or ["Capital"],
                    "is_global": is_global_v,
                    "location": ["Washington, D.C."], "rolling": False,
                    "fee_required": False, "fee_amount": None,
                    "cost_to_participate": False, "cost_amount": None,
                    "equity_percentage": False, "equity_details": None,
                    "safe_note": False, "safe_note_details": None,
                    "raw_source_data": {
                        "source":          "grantwatch_listing",
                        "snippet":         snippet,
                        "snippet_sponsor": snippet_sponsor,
                        "grantwatch_url":  grant_url,
                        "official_page":   official_page_url,
                    },
                    "extraction_confidence": round(min(confidence_v, 1.0), 2),
                }

                agency_level = infer_agency_level(title)
                agency       = None
                if enriched.get("agency_name"):
                    agency = get_or_create_agency(
                        db,
                        name        = enriched["agency_name"],
                        website_url = enriched.get("agency_website"),
                        state       = dc_state,
                        level       = agency_level,
                    )

                confidence   = enriched.get("extraction_confidence", 0.2)
                needs_review = (
                    confidence < 0.6
                    or enriched.get("fee_required")
                    or enriched.get("equity_percentage")
                    or enriched.get("safe_note")
                )

                description = enriched.get("description") or snippet or ""
                summary     = enriched.get("summary") or build_summary(description, title)
                award_min   = enriched.get("award_min")
                award_max   = enriched.get("award_max")
                tags        = detect_tags(title, description, category_name)
                is_rolling  = enriched.get("rolling", False) or (deadline is None)

                if is_rolling:
                    opp_status = OpportunityStatus.ROLLING
                elif needs_review:
                    opp_status = OpportunityStatus.UNVERIFIED
                else:
                    opp_status = OpportunityStatus.ACTIVE

                opportunity = Opportunity(
                    title            = title[:500],
                    sponsor_name     = enriched.get("sponsor_name"),
                    sponsor_website  = enriched.get("sponsor_website"),
                    logo_url         = enriched.get("logo_url"),
                    is_global        = enriched.get("is_global", False),
                    location         = enriched.get("location", ["Washington, D.C."]),
                    opportunity_url  = enriched.get("opportunity_url"),
                    application_url  = enriched.get("application_url") or "",
                    award_value      = format_award_value(award_min, award_max),
                    award_min        = award_min,
                    award_max        = award_max,
                    cash_award       = enriched.get("cash_award"),
                    posted_date      = None,
                    rolling          = is_rolling,
                    deadline         = deadline,
                    deadline_display = format_deadline_display(deadline),
                    opportunity_type = OpportunityType.GRANT,
                    category         = OpportunityCategory.GOVERNMENT,
                    status           = opp_status,
                    industry         = category_name,
                    tags             = tags,
                    sdg_alignment    = enriched.get("sdg_alignment", []),
                    opportunity_gap_resources = enriched.get("opportunity_gap_resources", []),
                    summary                   = summary[:500] if summary else None,
                    description               = description or None,
                    eligibility_requirements  = enriched.get("eligibility_requirements", []),
                    eligibility_individual    = enriched.get("eligibility_individual",   False),
                    eligibility_organization  = enriched.get("eligibility_organization", True),
                    global_locations          = enriched.get("location", []) if enriched.get("is_global") else None,
                    contact_names    = enriched.get("contact_names"),
                    contact_email    = enriched.get("contact_email"),
                    contact_phone    = enriched.get("contact_phone"),
                    fee_required        = enriched.get("fee_required",        False),
                    fee_amount          = enriched.get("fee_amount"),
                    cost_to_participate = enriched.get("cost_to_participate", False),
                    cost_amount         = enriched.get("cost_amount"),
                    equity_percentage   = enriched.get("equity_percentage",   False),
                    equity_details      = enriched.get("equity_details"),
                    safe_note           = enriched.get("safe_note",           False),
                    safe_note_details   = enriched.get("safe_note_details"),
                    source_id             = source.id,
                    agency_id             = agency.id if agency else None,
                    state_id              = dc_state.id if dc_state else None,
                    data_quality_score    = round(confidence, 2),
                    extraction_confidence = round(confidence, 2),
                    needs_review          = needs_review,
                    raw_source_data       = enriched.get("raw_source_data", {}),
                )

                try:
                    db.add(opportunity)
                    db.flush()
                    opportunity.categories.append(category)
                    if enriched.get("eligibility_individual"):
                        opportunity.eligible_applicants.append(at_individual)
                    if enriched.get("eligibility_organization"):
                        opportunity.eligible_applicants.append(at_nonprofit)
                        opportunity.eligible_applicants.append(at_business)
                    db.commit()
                    stats["new"] += 1
                    log.info(
                        "  Inserted id=%d  confidence=%.2f  needs_review=%s  "
                        "flags: fee=%s equity=%s safe=%s",
                        opportunity.id, confidence, needs_review,
                        enriched.get("fee_required"), enriched.get("equity_percentage"),
                        enriched.get("safe_note"),
                    )
                except Exception as exc:
                    db.rollback()
                    log.error("  Failed to insert %s: %s", title[:60], exc)
                    stats["error"] += 1

    except Exception as exc:
        log.error("Pipeline failed: %s", exc)
        db.rollback()
        raise

    finally:
        if source:
            try:
                scrape_log = ScrapeLog(
                    source_id      = source.id,
                    started_at     = scrape_started,
                    completed_at   = datetime.utcnow(),
                    grants_found   = stats["found"],
                    grants_new     = stats["new"],
                    grants_updated = 0,
                    status         = "success" if stats["error"] == 0 else "partial",
                )
                db.add(scrape_log)
                db.commit()
            except Exception as exc:
                log.error("Could not write scrape log: %s", exc)
        db.close()

    log.info("Pipeline finished")
    log.info("  Total found on GrantWatch : %d", stats["found"])
    log.info("  Filtered out              : %d", stats["filtered_out"])
    log.info("  Skipped duplicates        : %d", stats["skipped_duplicate"])
    log.info("  Inserted                  : %d", stats["new"])
    log.info("  Errors                    : %d", stats["error"])


if __name__ == "__main__":
    run_pipeline()
