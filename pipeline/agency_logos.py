"""
pipeline/agency_logos.py

Registry of agency logos keyed by domain.
Used by load_scraped_grants.py and sync_opportunities.py to populate logo_url.

Usage:
    from pipeline.agency_logos import get_logo_url

    logo = get_logo_url("https://esd.ny.gov/some-grant")   # → esd.ny.gov favicon

Verify all logos:
    python -m pipeline.agency_logos --verify
"""

from urllib.parse import urlparse
from typing import Optional

FALLBACK_LOGO_URL = "https://img.icons8.com/ios-filled/50/government.png"

# ─────────────────────────────────────────────────────────────────────────────
# Registry — one entry per unique source domain in ALL_SOURCES
# ─────────────────────────────────────────────────────────────────────────────

LOGO_REGISTRY: dict = {

    # ── New York ──────────────────────────────────────────────────────────────

    "esd.ny.gov": {
        "agency_name": "Empire State Development",
        "logo_url":    "https://esd.ny.gov/themes/custom/nyesd/src/images/favicon.ico",
        "state":       "NY",
    },
    "dos.ny.gov": {
        "agency_name": "NY Department of State",
        "logo_url":    "https://dos.ny.gov/profiles/custom/webny/themes/custom/webny_theme/favicon.ico",
        "state":       "NY",
    },
    "www.nysca.org": {
        "agency_name": "NY State Council on the Arts",
        "logo_url":    "https://dos.ny.gov/profiles/custom/webny/themes/custom/webny_theme/favicon.ico",  # nysca has no working favicon; use NY webny theme
        "state":       "NY",
    },
    "nysca.org": {
        "agency_name": "NY State Council on the Arts",
        "logo_url":    "https://dos.ny.gov/profiles/custom/webny/themes/custom/webny_theme/favicon.ico",
        "state":       "NY",
    },
    "www.health.ny.gov": {
        "agency_name": "NY Department of Health",
        "logo_url":    "https://www.health.ny.gov/favicon.ico",
        "state":       "NY",
    },
    "health.ny.gov": {
        "agency_name": "NY Department of Health",
        "logo_url":    "https://www.health.ny.gov/favicon.ico",
        "state":       "NY",
    },
    "ocfs.ny.gov": {
        "agency_name": "NY Office of Children & Family Services",
        "logo_url":    "https://ocfs.ny.gov/favicon.ico",
        "state":       "NY",
    },
    "www.nysed.gov": {
        "agency_name": "NY State Education Department",
        "logo_url":    "https://www.nysed.gov/favicon.ico",
        "state":       "NY",
    },
    "nysed.gov": {
        "agency_name": "NY State Education Department",
        "logo_url":    "https://www.nysed.gov/favicon.ico",
        "state":       "NY",
    },
    "hcr.ny.gov": {
        "agency_name": "NY Homes & Community Renewal",
        "logo_url":    "https://hcr.ny.gov/profiles/custom/webny/themes/custom/webny_theme/favicon.ico",
        "state":       "NY",
    },

    # ── Maryland ──────────────────────────────────────────────────────────────

    "mdot.maryland.gov": {
        "agency_name": "MD Department of Transportation",
        "logo_url":    "https://egov.maryland.gov/favicon.ico",  # mdot blocks direct access; use MD state egov favicon
        "state":       "MD",
    },
    "mde.maryland.gov": {
        "agency_name": "MD Department of the Environment",
        "logo_url":    "https://mde.maryland.gov/_layouts/15/MDSharePointToolkit/egov/img/favicon.ico?v=1",
        "state":       "MD",
    },
    "www.marylandpublicschools.org": {
        "agency_name": "MD State Department of Education",
        "logo_url":    "https://www.marylandpublicschools.org/PublishingImages/favicon.ico",
        "state":       "MD",
    },
    "marylandpublicschools.org": {
        "agency_name": "MD State Department of Education",
        "logo_url":    "https://www.marylandpublicschools.org/PublishingImages/favicon.ico",
        "state":       "MD",
    },
    "www.marbidco.org": {
        "agency_name": "MARBIDCO – Agriculture & Rural Business",
        "logo_url":    "https://www.marbidco.org/favicon.ico",
        "state":       "MD",
    },
    "marbidco.org": {
        "agency_name": "MARBIDCO – Agriculture & Rural Business",
        "logo_url":    "https://www.marbidco.org/favicon.ico",
        "state":       "MD",
    },
    "commerce.maryland.gov": {
        "agency_name": "Maryland Department of Commerce",
        "logo_url":    "https://commerce.maryland.gov/Style%20Library/egov/img/icons/favicon.ico",
        "state":       "MD",
    },
    "dhcd.maryland.gov": {
        "agency_name": "MD DHCD – Housing & Community Development",
        "logo_url":    "https://dhcd.maryland.gov/Style%20Library/egov/img/logo.png",
        "state":       "MD",
    },

    # ── Washington D.C. ───────────────────────────────────────────────────────

    "dmped.dc.gov": {
        "agency_name": "DC DMPED – Economic Development",
        "logo_url":    "https://dmped.dc.gov/favicon.ico",
        "state":       "DC",
    },
    "does.dc.gov": {
        "agency_name": "DC DOES – Workforce Development",
        "logo_url":    "https://does.dc.gov/favicon.ico",
        "state":       "DC",
    },
    "osse.dc.gov": {
        "agency_name": "DC OSSE – Office of the State Superintendent",
        "logo_url":    "https://osse.dc.gov/favicon.ico",
        "state":       "DC",
    },
    "dcgrants.dc.gov": {
        "agency_name": "DC Grants Portal",
        "logo_url":    "https://dc.gov/favicon.ico",  # dcgrants.dc.gov DNS fails; use dc.gov favicon
        "state":       "DC",
    },
    "dslbd.dc.gov": {
        "agency_name": "DC DSLBD – Small & Local Business Development",
        "logo_url":    "https://dslbd.dc.gov/favicon.ico",
        "state":       "DC",
    },
    "ovsjg.dc.gov": {
        "agency_name": "DC OVSJG – Office of Victim Services & Justice Grants",
        "logo_url":    "https://ovsjg.dc.gov/favicon.ico",
        "state":       "DC",
    },

    # ── Pennsylvania ──────────────────────────────────────────────────────────

    "www.pa.gov": {
        "agency_name": "Pennsylvania State Government",
        "logo_url":    "https://www.pa.gov/favicon.ico",
        "state":       "PA",
    },
    "pa.gov": {
        "agency_name": "Pennsylvania State Government",
        "logo_url":    "https://www.pa.gov/favicon.ico",
        "state":       "PA",
    },
    "www.dli.pa.gov": {
        "agency_name": "PA Department of Labor & Industry",
        "logo_url":    "https://www.dli.pa.gov/favicon.ico",
        "state":       "PA",
    },
    "dli.pa.gov": {
        "agency_name": "PA Department of Labor & Industry",
        "logo_url":    "https://www.dli.pa.gov/favicon.ico",
        "state":       "PA",
    },
    "www.dcnr.pa.gov": {
        "agency_name": "PA DCNR – Conservation & Natural Resources",
        "logo_url":    "https://www.dcnr.pa.gov/favicon.ico",
        "state":       "PA",
    },
    "dcnr.pa.gov": {
        "agency_name": "PA DCNR – Conservation & Natural Resources",
        "logo_url":    "https://www.dcnr.pa.gov/favicon.ico",
        "state":       "PA",
    },
    "www.pennvestinvestments.com": {
        "agency_name": "PennVEST – Water & Infrastructure Funding",
        "logo_url":    "https://www.pa.gov/favicon.ico",  # pennvestinvestments.com DNS fails; use pa.gov favicon
        "state":       "PA",
    },
    "pennvestinvestments.com": {
        "agency_name": "PennVEST – Water & Infrastructure Funding",
        "logo_url":    "https://www.pa.gov/favicon.ico",
        "state":       "PA",
    },
    "www.pema.pa.gov": {
        "agency_name": "PEMA – Emergency Management Agency",
        "logo_url":    "https://www.pema.pa.gov/favicon.ico",
        "state":       "PA",
    },
    "pema.pa.gov": {
        "agency_name": "PEMA – Emergency Management Agency",
        "logo_url":    "https://www.pema.pa.gov/favicon.ico",
        "state":       "PA",
    },
    "www.agriculture.pa.gov": {
        "agency_name": "PA Department of Agriculture",
        "logo_url":    "https://www.agriculture.pa.gov/favicon.ico",
        "state":       "PA",
    },
    "agriculture.pa.gov": {
        "agency_name": "PA Department of Agriculture",
        "logo_url":    "https://www.agriculture.pa.gov/favicon.ico",
        "state":       "PA",
    },
    "dced.pa.gov": {
        "agency_name": "PA DCED – Community & Economic Development",
        "logo_url":    "https://dced.pa.gov/favicon.ico",
        "state":       "PA",
    },
    "www.dced.pa.gov": {
        "agency_name": "PA DCED – Community & Economic Development",
        "logo_url":    "https://dced.pa.gov/favicon.ico",
        "state":       "PA",
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def get_logo_url(source_url: Optional[str]) -> str:
    """
    Extract the domain from source_url, look it up in LOGO_REGISTRY,
    and return the logo_url. Falls back to FALLBACK_LOGO_URL if not found.
    """
    if not source_url:
        return FALLBACK_LOGO_URL
    try:
        domain = urlparse(source_url).netloc.lower().strip()
        if not domain:
            return FALLBACK_LOGO_URL
        entry = LOGO_REGISTRY.get(domain)
        if entry:
            return entry["logo_url"]
    except Exception:
        pass
    return FALLBACK_LOGO_URL


# ─────────────────────────────────────────────────────────────────────────────
# CLI: --verify
# ─────────────────────────────────────────────────────────────────────────────

def _verify():
    import requests

    # Deduplicate by logo_url (www and non-www share the same URL)
    seen: dict[str, str] = {}  # logo_url → agency_name
    for domain, entry in LOGO_REGISTRY.items():
        url = entry["logo_url"]
        if url not in seen:
            seen[url] = entry["agency_name"]

    working = []
    broken  = []

    print(f"Verifying {len(seen)} unique logo URLs...\n")

    for logo_url, agency_name in sorted(seen.items(), key=lambda x: x[1]):
        try:
            r = requests.get(logo_url, timeout=10, allow_redirects=True,
                             headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"})
            ct = r.headers.get("content-type", "")
            ok = (r.status_code == 200 and
                  any(t in ct for t in ("image/", "application/octet-stream")))
            if ok:
                working.append((agency_name, logo_url, r.status_code, ct.split(";")[0]))
            else:
                broken.append((agency_name, logo_url, r.status_code, ct.split(";")[0]))
        except Exception as exc:
            broken.append((agency_name, logo_url, "ERR", str(exc)[:60]))

    print(f"{'─'*70}")
    print(f"WORKING ({len(working)})")
    print(f"{'─'*70}")
    for name, url, code, ct in working:
        print(f"  [{code}] {ct:<25}  {name}")
        print(f"         {url}")

    print(f"\n{'─'*70}")
    print(f"BROKEN ({len(broken)})")
    print(f"{'─'*70}")
    for name, url, code, ct in broken:
        print(f"  [{code}] {ct:<25}  {name}")
        print(f"         {url}")

    print(f"\n{'='*70}")
    print(f"SUMMARY: {len(working)} working / {len(broken)} broken / {len(seen)} total")
    print(f"{'='*70}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true",
                        help="Fetch each logo URL and report working vs broken")
    args = parser.parse_args()
    if args.verify:
        _verify()
    else:
        parser.print_help()
