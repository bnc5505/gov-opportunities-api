"""
Scraper Orchestrator
====================
Manages execution of all four state scrapers with:
  - Per-state retry logic (3 attempts, then skip)
  - Structured result tracking with partial-success handling
  - Direct integration with run_all_scrapers.run_all()

Integration:
  run_all_scrapers.run_all(states=["PA"]) already returns a list
  of per-source result dicts. We call it once per state so retry
  logic is isolated — if PA fails, NY/DC/MD are unaffected.

  Each source result dict from run_all() looks like:
    {
      "source": "NY Empire State Development",
      "state":  "NY",
      "total":  23,
      "active": 18,
      "rolling": 3,
      "needs_review": 2,
      "no_deadline": 0,
      "error":  None,          # or error string
      "output_file": "...",
    }
"""

import os
import time
import logging
import traceback
from datetime import datetime, timezone
from typing import Optional

from shared.run_logger import RunLogger

logger = logging.getLogger(__name__)


# ── State metadata (for logging and display) ──────────────────
#    Actual scraper sources live in run_all_scrapers.ALL_SOURCES.
#    This dict maps codes to display names and team owners.

STATE_META = {
    "MD": {
        "name": "Maryland",
        "owner": "Anthony Upshaw",
    },
    "PA": {
        "name": "Pennsylvania",
        "owner": "Rishitha Reddy Busi, Jaswanth Gowthu",
    },
    "NY": {
        "name": "New York",
        "owner": "Bhargav Ramineni",
    },
    "DC": {
        "name": "Washington D.C.",
        "owner": "Keerthika Bethala, Stephen Daniel Pakki",
    },
}

# MD first (reference implementation), then the rest
DEFAULT_STATE_ORDER = ["MD", "PA", "NY", "DC"]


class ScraperOrchestrator:
    """
    Orchestrates scraper execution across all states.

    Strategy:
      1. Run each state sequentially (avoids rate-limiting)
      2. Retry failed states up to max_retries times
      3. Skip a state after exhausting retries; continue others
      4. Log every attempt to the scraper_runs table
    """

    def __init__(
        self,
        run_id: str,
        run_logger: RunLogger,
        max_retries: Optional[int] = None,
        retry_delay: Optional[int] = None,
    ):
        self.run_id = run_id
        self.run_logger = run_logger
        self.max_retries = max_retries or int(os.getenv("SCRAPER_MAX_RETRIES", "3"))
        self.retry_delay = retry_delay or int(os.getenv("SCRAPER_RETRY_DELAY_SECONDS", "30"))

    def run_all(self) -> list[dict]:
        """Run all registered states in default order."""
        return self.run_states(DEFAULT_STATE_ORDER)

    def run_states(self, state_codes: list[str]) -> list[dict]:
        """Run specific states by their codes (e.g. ['PA', 'DC'])."""
        results = []

        for code in state_codes:
            code = code.upper().strip()
            if code not in STATE_META:
                logger.warning(f"Unknown state code: {code} — skipping")
                results.append({
                    "state": code,
                    "status": "skipped",
                    "reason": "Unknown state code",
                })
                continue

            result = self._run_single_state(code)
            results.append(result)

        return results

    def _run_single_state(self, state_code: str) -> dict:
        """
        Run all scrapers for one state with retry logic.

        Calls run_all_scrapers.run_all(states=[state_code]) which
        executes every source registered under that state code.

        Success criteria:
          - At least one source returned grants without error → success/partial
          - ALL sources errored → treat as state failure, trigger retry
        """
        meta = STATE_META[state_code]
        state_name = meta["name"]

        logger.info(f"{'─' * 50}")
        logger.info(f"Starting: {state_name} ({state_code})")
        logger.info(f"Owner:    {meta['owner']}")

        last_error = None

        for attempt in range(1, self.max_retries + 1):
            attempt_start = datetime.now(timezone.utc)
            logger.info(f"  Attempt {attempt}/{self.max_retries}...")

            try:
                source_results = self._execute_state(state_code)
                duration = (datetime.now(timezone.utc) - attempt_start).total_seconds()

                # ── Aggregate across all sources for this state ──
                total_grants = sum(r["total"] for r in source_results)
                total_active = sum(r["active"] for r in source_results)
                errored = [r for r in source_results if r["error"]]
                sources_ok = len(source_results) - len(errored)

                # If every single source failed, treat as full state failure
                if errored and sources_ok == 0:
                    error_summary = "; ".join(
                        f"{r['source']}: {r['error']}" for r in errored
                    )
                    raise RuntimeError(
                        f"All {len(errored)} sources failed — {error_summary[:300]}"
                    )

                # ── Build result ─────────────────────────────────
                status = "success" if not errored else "partial"

                result = {
                    "state":           state_code,
                    "name":            state_name,
                    "status":          status,
                    "attempt":         attempt,
                    "records_scraped": total_grants,
                    "active_grants":   total_active,
                    "sources_total":   len(source_results),
                    "sources_ok":      sources_ok,
                    "sources_failed":  len(errored),
                    "duration_seconds": round(duration, 2),
                    "timestamp":       attempt_start.isoformat(),
                }

                if errored:
                    result["warnings"] = [
                        f"{r['source']}: {r['error']}" for r in errored
                    ]
                    logger.warning(
                        f"  PARTIAL: {state_name} — {sources_ok}/{len(source_results)} "
                        f"sources OK, {total_grants} grants in {duration:.1f}s"
                    )
                else:
                    logger.info(
                        f"  SUCCESS: {state_name} — {sources_ok} sources, "
                        f"{total_grants} grants ({total_active} active) "
                        f"in {duration:.1f}s"
                    )

                # Log to DB
                self.run_logger.log_state_run(
                    run_id=self.run_id,
                    state_code=state_code,
                    status=status,
                    attempt=attempt,
                    records_count=total_grants,
                    duration=duration,
                )

                return result

            except Exception as e:
                last_error = str(e)
                duration = (datetime.now(timezone.utc) - attempt_start).total_seconds()

                logger.error(
                    f"  FAILED (attempt {attempt}): {state_name} — {last_error}"
                )
                logger.debug(traceback.format_exc())

                # Log failed attempt
                self.run_logger.log_state_run(
                    run_id=self.run_id,
                    state_code=state_code,
                    status="retry" if attempt < self.max_retries else "failed",
                    attempt=attempt,
                    records_count=0,
                    duration=duration,
                    error_message=last_error[:500],
                )

                if attempt < self.max_retries:
                    logger.info(f"  Waiting {self.retry_delay}s before retry...")
                    time.sleep(self.retry_delay)

        # ── All retries exhausted ────────────────────────────────
        logger.error(
            f"  SKIPPING {state_name} after {self.max_retries} failed attempts."
        )

        return {
            "state":           state_code,
            "name":            state_name,
            "status":          "failed",
            "attempt":         self.max_retries,
            "records_scraped": 0,
            "last_error":      last_error,
            "timestamp":       datetime.now(timezone.utc).isoformat(),
        }

    def _execute_state(self, state_code: str) -> list[dict]:
        """
        Call run_all_scrapers.run_all(states=[state_code]).

        This runs every source in ALL_SOURCES that matches the
        state code. For example, states=["NY"] runs all 7 NY
        scrapers (Empire, DOS, NYSCA, Health, OCFS, NYSED, Homes).

        Returns the list of per-source result dicts exactly as
        run_all() produces them.
        """
        from scrapers.run_all_scrapers import run_all

        logger.info(f"    Calling run_all(states=['{state_code}'])...")
        results = run_all(states=[state_code])

        if not results:
            raise RuntimeError(f"No sources registered for state {state_code}")

        return results
