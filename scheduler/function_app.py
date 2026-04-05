"""
Azure Functions Timer Trigger - Grant Scraper Scheduler
========================================================
Runs daily at 5:00 AM EST (10:00 UTC) to scrape state-level
funding opportunities across MD, PA, NY, and DC.

Integrates with the existing gov-opportunities-api codebase:
  - base_scraper.py (shared scraping framework)
  - run_all_scrapers.py (master runner)
  - Azure PostgreSQL (gov-grants-rg-msu)
  - Azure AI Foundry GPT-4o enrichment

Failure strategy: retry each state up to 3 times, then skip
and continue with remaining states.
"""

import azure.functions as func
import logging
import json
import os
from datetime import datetime, timezone

# Load .env from project root when running locally
# (Azure Functions production reads from Application Settings instead)
try:
    from dotenv import load_dotenv
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        load_dotenv(env_path)
except ImportError:
    pass

from shared.orchestrator import ScraperOrchestrator
from shared.run_logger import RunLogger

app = func.FunctionApp()


# ── Timer Trigger: 5 AM EST = 10:00 UTC ──────────────────────
#    Cron format: {second} {minute} {hour} {day} {month} {dow}

@app.timer_trigger(
    schedule="0 0 10 * * *",
    arg_name="timer",
    run_on_startup=False,
)
def daily_grant_scraper(timer: func.TimerRequest) -> None:
    """
    Main entry point. Fires once daily at 10:00 UTC (5 AM EST).
    Orchestrates all four state scrapers with per-state retry logic.
    """
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logger = logging.getLogger(f"scraper.run.{run_id}")

    if timer.past_due:
        logger.warning("Timer is past due — running immediately.")

    logger.info("=" * 60)
    logger.info(f"SCRAPER RUN {run_id} STARTED")
    logger.info(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 60)

    # Initialize components
    run_logger = RunLogger()
    orchestrator = ScraperOrchestrator(run_id=run_id, run_logger=run_logger)

    # Execute the full pipeline
    results = orchestrator.run_all()

    # Summary
    succeeded = [r for r in results if r["status"] == "success"]
    failed = [r for r in results if r["status"] == "failed"]
    skipped = [r for r in results if r["status"] == "skipped"]

    summary = {
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_states": len(results),
        "succeeded": len(succeeded),
        "failed": len(failed),
        "skipped": len(skipped),
        "details": results,
    }

    logger.info("=" * 60)
    logger.info(f"RUN COMPLETE: {len(succeeded)} succeeded, "
                f"{len(failed)} failed, {len(skipped)} skipped")
    logger.info("=" * 60)

    # Persist the run summary
    run_logger.log_run_summary(summary)

    # Log full summary as structured output for Azure Monitor
    logging.info(json.dumps(summary, indent=2, default=str))


# ── HTTP Trigger: Manual run + health check ───────────────────
#    Allows the team to trigger a scrape manually or check status.

@app.route(route="scraper/run", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def manual_scraper_trigger(req: func.HttpRequest) -> func.HttpResponse:
    """
    POST /api/scraper/run
    Manually trigger a scraper run outside the daily schedule.
    Useful for testing or re-running after a fix.
    """
    run_id = datetime.now(timezone.utc).strftime("MANUAL_%Y%m%d_%H%M%S")
    logger = logging.getLogger(f"scraper.manual.{run_id}")
    logger.info(f"Manual trigger received: {run_id}")

    # Check if a specific state was requested
    try:
        body = req.get_json()
        states = body.get("states", None)  # e.g. ["PA", "DC"]
    except ValueError:
        states = None

    run_logger = RunLogger()
    orchestrator = ScraperOrchestrator(run_id=run_id, run_logger=run_logger)

    if states:
        results = orchestrator.run_states(states)
    else:
        results = orchestrator.run_all()

    succeeded = sum(1 for r in results if r["status"] == "success")
    failed = sum(1 for r in results if r["status"] == "failed")

    return func.HttpResponse(
        json.dumps({"run_id": run_id, "results": results}, default=str),
        status_code=200 if failed == 0 else 207,
        mimetype="application/json",
    )


@app.route(route="scraper/status", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def scraper_status(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/scraper/status
    Returns the last 10 scraper runs for quick health checks.
    """
    run_logger = RunLogger()
    recent_runs = run_logger.get_recent_runs(limit=10)

    return func.HttpResponse(
        json.dumps(recent_runs, default=str),
        status_code=200,
        mimetype="application/json",
    )
