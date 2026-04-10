"""
Quick Scheduler Test
────────────────────
Run from your project root to test the scheduler orchestrator.
Loads credentials from the same .env file your scrapers use.

Usage:
    python test_scheduler.py --dry-run        # test wiring only (no scraping)
    python test_scheduler.py PA               # run just Pennsylvania
    python test_scheduler.py PA DC            # run PA and DC
    python test_scheduler.py                  # run all states
"""

import sys
import os
import logging

# ── Load .env from project root (same file your scrapers use) ─
try:
    from dotenv import load_dotenv
    load_dotenv()  # reads .env from current directory
    print("Loaded .env")
except ImportError:
    print("python-dotenv not installed — reading from environment only")

# ── Add scheduler/ to path so shared.* imports work ──────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scheduler"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)

from datetime import datetime, timezone
from shared.orchestrator import ScraperOrchestrator, STATE_META
from shared.run_logger import RunLogger


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    dry_run = "--dry-run" in sys.argv

    states = [s.upper() for s in args] if args else list(STATE_META.keys())

    run_id = datetime.now(timezone.utc).strftime("TEST_%Y%m%d_%H%M%S")

    print(f"\n{'=' * 60}")
    print(f"  SCHEDULER TEST — Run ID: {run_id}")
    print(f"  States:  {states}")
    print(f"  DB URL:  {os.getenv('DATABASE_URL', 'NOT SET')[:60]}...")
    print(f"  Dry run: {dry_run}")
    print(f"{'=' * 60}\n")

    if dry_run:
        # ── Step 1: Test that scraper imports work ────────────
        print("[1/3] Testing scraper imports...")
        try:
            from scrapers.run_all_scrapers import ALL_SOURCES
            for code in states:
                sources = [s for s in ALL_SOURCES if s["state"] == code]
                print(f"  {code}: {len(sources)} sources registered")
                for s in sources:
                    print(f"       - {s['name']}")
            print("  OK\n")
        except Exception as e:
            print(f"  FAILED: {e}\n")
            return

        # ── Step 2: Test DB connection ────────────────────────
        print("[2/3] Testing database connection...")
        try:
            run_logger = RunLogger()
            print(f"  scraper_runs table: OK")
            print(f"  DB type: {'SQLite' if run_logger.is_sqlite else 'PostgreSQL'}\n")
        except Exception as e:
            print(f"  FAILED: {e}\n")
            return

        # ── Step 3: Test writing a dummy log entry ────────────
        print("[3/3] Testing log write...")
        try:
            run_logger.log_state_run(
                run_id="DRY_RUN_TEST",
                state_code="XX",
                status="test",
                attempt=1,
                records_count=0,
                duration=0.0,
                error_message="Dry run test entry — safe to delete",
            )
            print("  Write: OK\n")
        except Exception as e:
            print(f"  FAILED: {e}\n")
            return

        print("All checks passed. Ready to run for real:")
        print(f"  python test_scheduler.py {' '.join(states)}\n")
        return

    # ── Full test: actually run scrapers ──────────────────────
    run_logger = RunLogger()
    orchestrator = ScraperOrchestrator(
        run_id=run_id,
        run_logger=run_logger,
        max_retries=1,       # 1 attempt during testing (faster)
        retry_delay=5,       # short delay
    )

    results = orchestrator.run_states(states)

    # ── Print summary ─────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print(f"  RESULTS")
    print(f"{'=' * 60}")

    for r in results:
        icons = {"success": "+", "partial": "~", "failed": "X", "skipped": "-"}
        icon = icons.get(r["status"], "?")
        grants = r.get("records_scraped", 0)
        src_ok = r.get("sources_ok", "?")
        src_total = r.get("sources_total", "?")
        print(f"  [{icon}] {r['state']} ({r.get('name', '')}) — "
              f"{r['status']} — {grants} grants — "
              f"{src_ok}/{src_total} sources")

        if r.get("warnings"):
            for w in r["warnings"]:
                print(f"      ! {w[:80]}")
        if r.get("last_error"):
            print(f"      X {r['last_error'][:80]}")

    total = sum(r.get("records_scraped", 0) for r in results)
    print(f"\n  Total grants: {total}")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()   