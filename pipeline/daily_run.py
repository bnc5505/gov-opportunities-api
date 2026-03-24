#!/usr/bin/env python3
"""
daily_run.py

Orchestrates the full data pipeline in order:

  Step 1a — PA DCED scraper          → data/pa/pa_dced_grants_raw.json
  Step 1b — Multi-state scrapers      → data/{state}/*_raw.json
             (NY, MD, DC, PA — 22+ sources via scrapers/run_all_scrapers.py)
  Step 2  — load_scraped_grants.py   → scraped_grants table (clean + load all JSONs)
  Step 3  — find_deadlines.py        → resolve missing deadlines (L1 regex + L2 HTTP)
  Step 4  — enrich_scraped_grants.py → AI enrichment (skipped if no AZURE key)
  Step 5  — sync_opportunities.py    → scraped_grants → opportunities (upsert)

Run from project root:
    python pipeline/daily_run.py [--dry-run] [--skip-scrape] [--skip-enrich]

Flags:
    --dry-run         Pass --dry-run to sync step (no DB writes in final upsert)
    --skip-scrape     Skip ALL scrapers (use existing JSON files)
    --skip-pa         Skip only the PA DCED scraper
    --skip-multistate Skip only the multi-state scrapers
    --skip-enrich     Skip Azure AI enrichment (e.g. no API key)
    --skip-deadlines  Skip deadline-finding step
    --states          State filter for multi-state scraper (e.g. --states NY MD)
"""

import sys
import os
import subprocess
import argparse
import logging
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent   # pipeline/../ = project root
PYTHON       = str(PROJECT_ROOT / ".venv" / "bin" / "python")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("daily_run")


def run_step(name: str, cmd: list, cwd=None) -> bool:
    """Run a subprocess step. Returns True on success, False on failure."""
    log.info("=" * 60)
    log.info("STEP: %s", name)
    log.info("CMD:  %s", " ".join(cmd))
    log.info("=" * 60)
    start = time.time()

    result = subprocess.run(cmd, cwd=cwd or str(PROJECT_ROOT), capture_output=False)
    elapsed = time.time() - start

    if result.returncode == 0:
        log.info("  [OK] %s  (%.1fs)", name, elapsed)
        return True
    else:
        log.error("  [FAIL] %s returned exit code %d  (%.1fs)", name, result.returncode, elapsed)
        return False


def main():
    parser = argparse.ArgumentParser(description="Daily pipeline orchestration")
    parser.add_argument("--dry-run",          action="store_true", help="Dry-run the sync step")
    parser.add_argument("--skip-scrape",      action="store_true", help="Skip ALL scrapers")
    parser.add_argument("--skip-pa",          action="store_true", help="Skip PA DCED scraper only")
    parser.add_argument("--skip-multistate",  action="store_true", help="Skip multi-state scrapers only")
    parser.add_argument("--skip-enrich",      action="store_true", help="Skip Azure AI enrichment")
    parser.add_argument("--skip-deadlines",   action="store_true", help="Skip deadline-finder step")
    parser.add_argument("--states",           nargs="*",           help="State filter for multi-state scraper (e.g. --states NY MD)")
    args = parser.parse_args()

    started_at = datetime.utcnow()
    log.info("Daily pipeline starting at %s", started_at.strftime("%Y-%m-%d %H:%M:%S UTC"))

    results = {}

    # Step 1a: PA DCED scraper
    skip_pa = args.skip_scrape or args.skip_pa
    if not skip_pa:
        ok = run_step(
            "PA DCED Scraper",
            [PYTHON, "-m", "scrapers.pa.pa_dced_scraper"],
            cwd=str(PROJECT_ROOT),
        )
        results["scrape_pa_dced"] = ok
        if not ok:
            log.warning("PA DCED scraper failed — continuing with existing JSON files")
    else:
        log.info("SKIP: PA DCED scraper")
        results["scrape_pa_dced"] = None

    # Step 1b: multi-state scrapers (NY, MD, DC, PA additional sources)
    skip_multi = args.skip_scrape or args.skip_multistate
    if not skip_multi:
        multi_cmd = [PYTHON, "-m", "scrapers.run_all_scrapers"]
        if args.states:
            multi_cmd += ["--state"] + [s.upper() for s in args.states]
        ok = run_step(
            "Multi-state Scrapers (NY / MD / DC / PA)",
            multi_cmd,
            cwd=str(PROJECT_ROOT),
        )
        results["scrape_multistate"] = ok
        if not ok:
            log.warning("Multi-state scraper had failures — continuing with existing JSON files")
    else:
        log.info("SKIP: multi-state scrapers")
        results["scrape_multistate"] = None

    # Step 2: load JSON → scraped_grants
    ok = run_step(
        "Load scraped_grants",
        [PYTHON, str(PROJECT_ROOT / "pipeline" / "load_scraped_grants.py")],
    )
    results["load"] = ok
    if not ok:
        log.error("Load step failed — aborting pipeline")
        _print_summary(results, started_at)
        sys.exit(1)

    # Step 3: resolve missing deadlines
    if not args.skip_deadlines:
        deadline_cmd = [
            PYTHON, str(PROJECT_ROOT / "pipeline" / "find_deadlines.py"),
            "--skip-ai",   # only use L1 regex + L2 HTTP (no Azure key needed)
        ]
        ok = run_step("Find Deadlines (L1+L2)", deadline_cmd)
        results["deadlines"] = ok
        if not ok:
            log.warning("Deadline step had issues — continuing anyway")
    else:
        log.info("SKIP: deadline-finder (--skip-deadlines)")
        results["deadlines"] = None

    # Step 4: AI enrichment
    if not args.skip_enrich:
        az_key = os.getenv("AZURE_OPENAI_API_KEY") or _read_env_key()
        if not az_key:
            log.warning("AZURE_OPENAI_API_KEY not set — skipping enrichment (pass --skip-enrich to suppress)")
            results["enrich"] = None
        else:
            ok = run_step(
                "AI Enrichment",
                [PYTHON, str(PROJECT_ROOT / "pipeline" / "enrich_scraped_grants.py")],
            )
            results["enrich"] = ok
            if not ok:
                log.warning("Enrichment step had issues — continuing anyway")
    else:
        log.info("SKIP: AI enrichment (--skip-enrich)")
        results["enrich"] = None

    # Step 5: sync → opportunities
    sync_cmd = [PYTHON, str(PROJECT_ROOT / "pipeline" / "sync_opportunities.py")]
    if args.dry_run:
        sync_cmd.append("--dry-run")

    ok = run_step("Sync opportunities", sync_cmd)
    results["sync"] = ok
    if not ok:
        log.error("Sync step failed")

    _print_summary(results, started_at)
    sys.exit(0 if all(v is not False for v in results.values()) else 1)


def _read_env_key() -> str:
    """Try to read AZURE_OPENAI_API_KEY from the project .env file."""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return ""
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("AZURE_OPENAI_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _print_summary(results: dict, started_at: datetime):
    elapsed = (datetime.utcnow() - started_at).total_seconds()
    print(f"\n{'='*60}")
    print(f"DAILY PIPELINE COMPLETE  ({elapsed:.0f}s)")
    print(f"{'='*60}")
    labels = {
        "scrape_pa_dced":    "PA DCED scrape",
        "scrape_multistate": "Multi-state scrapers (NY/MD/DC/PA)",
        "load":              "Load JSON → scraped_grants",
        "deadlines":         "Deadline finder",
        "enrich":            "AI enrichment",
        "sync":              "Sync → opportunities",
    }
    for key, label in labels.items():
        status = results.get(key)
        if status is True:
            icon = "OK"
        elif status is False:
            icon = "FAILED"
        else:
            icon = "SKIPPED"
        print(f"  {icon:<8}  {label}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
