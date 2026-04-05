"""
Scraper Status Router (Optional)
=================================
Drop this into your existing FastAPI app to expose scraper
run history through your main API alongside grant endpoints.

Usage in your main app:
    from app.routers.scraper_status import router as scraper_router
    app.include_router(scraper_router)

This queries the same scraper_runs table that the Azure Function
writes to, giving Runwei's frontend (or your team) visibility
into pipeline health without accessing Azure Portal.
"""

from fastapi import APIRouter, Query
from typing import Optional
import psycopg2
from psycopg2.extras import RealDictCursor
import os

router = APIRouter(prefix="/scraper", tags=["Scraper Status"])


def _get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        sslmode=os.getenv("DB_SSLMODE", "require"),
    )


@router.get("/runs")
def get_recent_runs(limit: int = Query(default=10, le=50)):
    """Get the most recent scraper run summaries."""
    conn = _get_conn()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT run_id, status, records_scraped, run_type, created_at
            FROM scraper_runs
            WHERE state_code = 'ALL'
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get("/runs/{run_id}")
def get_run_detail(run_id: str):
    """Get per-state details for a specific run."""
    conn = _get_conn()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT state_code, state_name, status, attempt,
                   records_scraped, duration_seconds, error_message, created_at
            FROM scraper_runs
            WHERE run_id = %s AND state_code != 'ALL'
            ORDER BY created_at
            """,
            (run_id,),
        )
        rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get("/health")
def scraper_health():
    """
    Quick health check: was the last scheduled run successful?
    Returns a simple status Runwei's frontend could poll.
    """
    conn = _get_conn()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT run_id, status, records_scraped, created_at
            FROM scraper_runs
            WHERE state_code = 'ALL' AND run_type = 'scheduled'
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
    conn.close()

    if not row:
        return {"status": "no_data", "message": "No scheduled runs found yet"}

    return {
        "status": "healthy" if row["status"] == "success" else "degraded",
        "last_run": row["run_id"],
        "last_status": row["status"],
        "records_scraped": row["records_scraped"],
        "last_run_time": row["created_at"],
    }
