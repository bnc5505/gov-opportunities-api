"""
Master Scraper Runner
─────────────────────
Runs all state scrapers in sequence and saves to data/{state}/.
Each scraper: listing page → detail pages → PDFs → Azure AI → JSON.

Usage (from project root):
    python -m scrapers.run_all_scrapers               # all states
    python -m scrapers.run_all_scrapers --state NY    # one state
    python -m scrapers.run_all_scrapers --state PA DC # specific states
"""

import os
import sys
import json
import argparse
import logging
from datetime import datetime
from typing import List, Dict

THIS_DIR     = os.path.dirname(os.path.abspath(__file__))   # scrapers/
PROJECT_ROOT = os.path.dirname(THIS_DIR)                    # project root
BASE_DIR     = os.path.join(THIS_DIR, "base")               # scrapers/base/

sys.path.insert(0, BASE_DIR)        # finds state_scraper_template and base_scraper
sys.path.insert(0, PROJECT_ROOT)    # finds scrapers.pa.*, scrapers.md.* etc.

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


# Source registry — "module" is the scraper to import, "config" is passed to run()

_GW = [
    "grant", "grants", "funding", "fund", "opportunity", "opportunities",
    "program", "programs", "award", "awards", "loan", "incentive",
    "assistance", "rebate", "scholarship", "fellowship", "rfp", "rfq", "notice",
]
_SK = [
    "login", "logout", "signin", "contact", "about", "news", "press",
    "events", "sitemap", "privacy", "terms", "facebook", "twitter",
    "linkedin", "youtube", "instagram", "careers", "jobs", "faq",
    "accessibility", "disclaimer", "copyright",
]


def _cfg(scraper_id, state, listing_url, base_url, domain, output_file,
         grant_keywords=None, skip_keywords=None):
    """Build a standard scraper config dict. Output goes to data/{state.lower()}/."""
    state_data_dir = os.path.join(PROJECT_ROOT, "data", state.lower())
    os.makedirs(state_data_dir, exist_ok=True)
    return {
        "scraper_id":     scraper_id,
        "state":          state,
        "listing_url":    listing_url,
        "base_url":       base_url,
        "domain":         domain,
        "output_file":    output_file,
        "data_dir":       state_data_dir,
        "grant_keywords": grant_keywords or _GW,
        "skip_keywords":  skip_keywords or _SK,
    }


ALL_SOURCES: List[Dict] = [

    # New York

    {
        "state":  "NY", "name": "NY Empire State Development",
        "type":   "custom",
        "module": "scrapers.ny.ny_empire_scraper",
    },
    {
        "state":  "NY", "name": "NY Dept of State – Community Grants",
        "type":   "custom",
        "module": "scrapers.ny.ny_dos_scraper",
    },
    {
        "state":  "NY", "name": "NY State Council on the Arts",
        "type":   "custom",
        "module": "scrapers.ny.ny_nysca_scraper",
    },
    {
        "state":  "NY", "name": "NY Dept of Health – Grant Programs",
        "type":   "custom",
        "module": "scrapers.ny.ny_health_scraper",
    },
    {
        "state":  "NY", "name": "NY Office of Children & Family Services",
        "type":   "custom",
        "module": "scrapers.ny.ny_ocfs_scraper",
    },
    {
        "state":  "NY", "name": "NY State Education Dept – Grants",
        "type":   "custom",
        "module": "scrapers.ny.ny_nysed_scraper",
    },
    {
        "state":  "NY", "name": "NY Homes & Community Renewal",
        "type":   "custom",
        "module": "scrapers.ny.ny_homes_scraper",
    },

    # Maryland

    {
        "state":  "MD", "name": "MD Dept of Transportation – Grants",
        "module": "state_scraper_template",
        "config": _cfg(
            "md_mdot", "MD",
            "https://mdot.maryland.gov/newMDOT/About/MDOT_Grants_and_Awards.html",
            "https://mdot.maryland.gov", "mdot.maryland.gov",
            "md_mdot_grants_raw.json",
        ),
    },
    {
        "state":  "MD", "name": "MD Dept of the Environment – Grants",
        "module": "state_scraper_template",
        "config": _cfg(
            "md_mde", "MD",
            "https://mde.maryland.gov/programs/Pages/Grants.aspx",
            "https://mde.maryland.gov", "mde.maryland.gov",
            "md_mde_grants_raw.json",
        ),
    },
    {
        "state":  "MD", "name": "MD State Dept of Education – Grants",
        "module": "state_scraper_template",
        "config": _cfg(
            "md_msde", "MD",
            "https://www.marylandpublicschools.org/about/Pages/Finance/Grants/index.aspx",
            "https://www.marylandpublicschools.org", "marylandpublicschools.org",
            "md_msde_grants_raw.json",
        ),
    },
    {
        "state":  "MD", "name": "MARBIDCO – Agriculture & Rural Business",
        "module": "state_scraper_template",
        "config": _cfg(
            "md_marbidco", "MD",
            "https://www.marbidco.org/",
            "https://www.marbidco.org", "marbidco.org",
            "md_marbidco_grants_raw.json",
        ),
    },
    {
        "state":  "MD", "name": "Choose Maryland – Economic Incentives",
        "module": "state_scraper_template",
        "config": _cfg(
            "md_commerce", "MD",
            "https://commerce.maryland.gov/fund",
            "https://commerce.maryland.gov", "commerce.maryland.gov",
            "md_commerce_grants_raw.json",
        ),
    },
    {
        "state":  "MD", "name": "MD DHCD – Housing & Community Development",
        "module": "state_scraper_template",
        "config": _cfg(
            "md_dhcd", "MD",
            "https://dhcd.maryland.gov/Pages/default.aspx",
            "https://dhcd.maryland.gov", "dhcd.maryland.gov",
            "md_dhcd_grants_raw.json",
        ),
    },

    # Washington D.C.

    {
        "state":  "DC", "name": "DC DMPED – Economic Development Grants",
        "module": "state_scraper_template",
        "config": _cfg(
            "dc_dmped", "DC",
            "https://dmped.dc.gov/service/grants",
            "https://dmped.dc.gov", "dmped.dc.gov",
            "dc_dmped_grants_raw.json",
        ),
    },
    {
        "state":  "DC", "name": "DC DOES – Workforce Grants",
        "module": "state_scraper_template",
        "config": _cfg(
            "dc_does", "DC",
            "https://does.dc.gov/page/grants-information",
            "https://does.dc.gov", "does.dc.gov",
            "dc_does_grants_raw.json",
        ),
    },
    {
        "state":  "DC", "name": "DC OSSE – Education Grants",
        "module": "state_scraper_template",
        "config": _cfg(
            "dc_osse", "DC",
            "https://osse.dc.gov/service/grants-osse",
            "https://osse.dc.gov", "osse.dc.gov",
            "dc_osse_grants_raw.json",
        ),
    },
    {
        "state":  "DC", "name": "DC Grants Portal",
        "module": "state_scraper_template",
        "config": _cfg(
            "dc_grants_portal", "DC",
            "https://dcgrants.dc.gov/",
            "https://dcgrants.dc.gov", "dcgrants.dc.gov",
            "dc_grants_portal_raw.json",
        ),
    },
    {
        "state":  "DC", "name": "DC DSLBD – Small Business Grants",
        "module": "state_scraper_template",
        "config": _cfg(
            "dc_dslbd", "DC",
            "https://dslbd.dc.gov/service/grants-and-incentives",
            "https://dslbd.dc.gov", "dslbd.dc.gov",
            "dc_dslbd_grants_raw.json",
        ),
    },
    {
        "state":  "DC", "name": "DC OVSJG – Justice Grants",
        "module": "state_scraper_template",
        "config": _cfg(
            "dc_ovsjg", "DC",
            "https://ovsjg.dc.gov/page/funding-opportunities-current",
            "https://ovsjg.dc.gov", "ovsjg.dc.gov",
            "dc_ovsjg_grants_raw.json",
        ),
    },

    # Pennsylvania
    # NOTE: PA DCED (pa_dced_scraper.py) is run separately in daily_run.py
    # because it has its own deep multi-layer scraper.

    {
        "state":  "PA", "name": "PA Official Grants Directory",
        "type":   "custom",
        "module": "scrapers.pa.pa_gov_grants_scraper",
    },
    {
        "state":  "PA", "name": "PA Dept of Labor & Industry – Workforce Grants",
        "type":   "custom",
        "module": "scrapers.pa.pa_dli_scraper",
    },
    {
        "state":  "PA", "name": "PA DCNR – Conservation & Recreation Grants",
        "type":   "custom",
        "module": "scrapers.pa.pa_dcnr_scraper",
    },
    {
        "state":  "PA", "name": "PennVEST – Water & Infrastructure Funding",
        "type":   "custom",
        "module": "scrapers.pa.pa_pennvest_scraper",
    },
    {
        "state":  "PA", "name": "PEMA – Emergency Management Grants",
        "type":   "custom",
        "module": "scrapers.pa.pa_pema_scraper",
    },
    {
        "state":  "PA", "name": "PA Dept of Agriculture – Grants",
        "type":   "custom",
        "module": "scrapers.pa.pa_agriculture_scraper",
    },
]


def run_scraper(source: Dict) -> Dict:
    """Run one scraper and return stats."""
    import importlib

    log.info(f"\n{'═'*70}")
    log.info(f"SCRAPING: {source['name']} ({source['state']})")
    log.info(f"{'═'*70}")

    try:
        module = importlib.import_module(source["module"])

        # Custom scrapers have config baked in; template scrapers receive a config dict.
        if source.get("type") == "custom":
            grants = module.run(save_json=True)
        else:
            grants = module.run(
                config=source["config"],
                save_json=True,
                load_db=False,
            )

        active  = [g for g in grants if g.get("status") == "active"]
        rolling = [g for g in grants if g.get("rolling")]
        review  = [g for g in grants if g.get("needs_review")]
        no_date = [g for g in grants if not g.get("deadline") and not g.get("rolling")]

        log.info(
            f"  Done: {len(grants)} grants | active={len(active)} "
            f"rolling={len(rolling)} review={len(review)} no_date={len(no_date)}"
        )

        cfg = source.get("config", {})
        out_file = (
            os.path.join(os.path.join(PROJECT_ROOT, "data", source["state"].lower()),
                         cfg["output_file"])
            if cfg else None
        )

        return {
            "source":       source["name"],
            "state":        source["state"],
            "output_file":  out_file,
            "total":        len(grants),
            "active":       len(active),
            "rolling":      len(rolling),
            "needs_review": len(review),
            "no_deadline":  len(no_date),
            "error":        None,
        }

    except Exception as e:
        log.error(f"  Scraper FAILED: {e}", exc_info=True)
        return {
            "source":       source["name"],
            "state":        source["state"],
            "output_file":  None,
            "total":        0,
            "active":       0,
            "rolling":      0,
            "needs_review": 0,
            "no_deadline":  0,
            "error":        str(e),
        }


def run_all(states: List[str] = None) -> List[Dict]:
    """Run all scrapers (optionally filtered by state). Returns list of result dicts."""
    start   = datetime.now()
    sources = ALL_SOURCES

    if states:
        upper = [s.upper() for s in states]
        sources = [s for s in ALL_SOURCES if s["state"] in upper]

    log.info(f"Running {len(sources)} scrapers for states: "
             f"{sorted(set(s['state'] for s in sources))}")

    results = []
    for source in sources:
        result = run_scraper(source)
        results.append(result)

    duration = (datetime.now() - start).total_seconds()

    print(f"\n{'='*70}")
    print(f"ALL SCRAPERS COMPLETE — {duration:.0f}s ({duration/60:.1f} min)")
    print(f"{'='*70}")
    print(f"  {'State':<5}  {'Status':<7}  {'Total':>5}  {'Active':>6}  {'Rolling':>7}  {'Review':>6}  Source")
    print(f"  {'-'*5}  {'-'*7}  {'-'*5}  {'-'*6}  {'-'*7}  {'-'*6}  {'-'*40}")

    total_grants = 0
    for r in results:
        status = "ERROR" if r["error"] else "OK"
        print(
            f"  {r['state']:<5}  {status:<7}  {r['total']:>5}  "
            f"{r['active']:>6}  {r['rolling']:>7}  {r['needs_review']:>6}  "
            f"{r['source'][:40]}"
        )
        if r["error"]:
            print(f"           └─ {r['error'][:80]}")
        total_grants += r["total"]

    print(f"\n  TOTAL GRANTS SCRAPED: {total_grants}")
    print(f"  Output directory:     {os.path.join(PROJECT_ROOT, 'data', '<state>')}")
    print(f"{'='*70}\n")

    summary = {
        "run_at":       datetime.utcnow().isoformat(),
        "duration_sec": duration,
        "total_grants": total_grants,
        "sources":      results,
    }
    summary_path = os.path.join(PROJECT_ROOT, "scraper_run_summary.json")
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    log.info(f"Summary saved → {summary_path}")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run all state grant scrapers")
    parser.add_argument(
        "--state", nargs="+",
        help="Filter to specific state codes (e.g. NY MD DC PA)"
    )
    args = parser.parse_args()
    run_all(states=args.state)
