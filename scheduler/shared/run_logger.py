"""
Scraper Run Logger
==================
Logs every scraper execution to the `scraper_runs` table
in the project's database (reads DATABASE_URL from .env).

Supports both:
  - SQLite  (local dev):   sqlite:///./gov_grants.db
  - PostgreSQL (Azure):    postgresql://user:pass@host:5432/db?sslmode=require

Table is auto-created on first use via _ensure_table().
"""

import os
import json
import logging
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def _parse_database_url(url: str) -> dict:
    """
    Parse DATABASE_URL into connection params.
    Handles both postgresql:// and sqlite:// schemes.
    """
    if url.startswith("sqlite"):
        return {"type": "sqlite", "path": url.replace("sqlite:///", "")}

    parsed = urlparse(url)
    params = {
        "type": "postgresql",
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "dbname": parsed.path.lstrip("/"),
        "user": parsed.username,
        "password": parsed.password,
    }

    # Extract query params like sslmode=require
    if parsed.query:
        for param in parsed.query.split("&"):
            if "=" in param:
                key, val = param.split("=", 1)
                params[key] = val

    return params


class RunLogger:
    """Manages scraper run logging to the project database."""

    def __init__(self):
        self.database_url = os.getenv("DATABASE_URL", "sqlite:///./gov_grants.db")
        self.db_config = _parse_database_url(self.database_url)
        self.is_sqlite = self.db_config["type"] == "sqlite"
        self._ensure_table()

    def _get_connection(self):
        """Create a database connection based on DATABASE_URL."""
        if self.is_sqlite:
            import sqlite3
            return sqlite3.connect(self.db_config["path"])
        else:
            import psycopg2
            conn_params = {
                "host": self.db_config["host"],
                "port": self.db_config["port"],
                "dbname": self.db_config["dbname"],
                "user": self.db_config["user"],
                "password": self.db_config["password"],
            }
            if self.db_config.get("sslmode"):
                conn_params["sslmode"] = self.db_config["sslmode"]
            return psycopg2.connect(**conn_params)

    def _placeholder(self):
        """Return the correct placeholder for the DB type."""
        return "?" if self.is_sqlite else "%s"

    def _serial_type(self):
        """Return the correct auto-increment type."""
        return "INTEGER PRIMARY KEY AUTOINCREMENT" if self.is_sqlite else "SERIAL PRIMARY KEY"

    def _timestamp_default(self):
        """Return the correct timestamp default."""
        return "DATETIME DEFAULT CURRENT_TIMESTAMP" if self.is_sqlite else "TIMESTAMPTZ DEFAULT NOW()"

    def _ensure_table(self):
        """
        Create the scraper_runs table if it does not exist.
        Works on both SQLite (local) and PostgreSQL (Azure).
        """
        p = self._placeholder()
        serial = self._serial_type()
        ts = self._timestamp_default()

        create_sql = f"""
        CREATE TABLE IF NOT EXISTS scraper_runs (
            id              {serial},
            run_id          VARCHAR(50) NOT NULL,
            state_code      VARCHAR(5),
            state_name      VARCHAR(50),
            status          VARCHAR(20) NOT NULL,
            attempt         INTEGER DEFAULT 1,
            records_scraped INTEGER DEFAULT 0,
            duration_seconds NUMERIC(10,2),
            error_message   TEXT,
            run_type        VARCHAR(20) DEFAULT 'scheduled',
            created_at      {ts}
        )
        """

        try:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute(create_sql)

            if not self.is_sqlite:
                # PostgreSQL indexes
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_scraper_runs_run_id
                        ON scraper_runs (run_id)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_scraper_runs_created_at
                        ON scraper_runs (created_at DESC)
                """)

            conn.commit()
            cur.close()
            conn.close()
            db_label = "SQLite" if self.is_sqlite else f"PostgreSQL ({self.db_config['host']})"
            logger.info(f"scraper_runs table ready on {db_label}")
        except Exception as e:
            logger.warning(f"Could not ensure scraper_runs table: {e}")

    def log_state_run(
        self,
        run_id: str,
        state_code: str,
        status: str,
        attempt: int = 1,
        records_count: int = 0,
        duration: float = 0.0,
        error_message: Optional[str] = None,
    ):
        """Log a single state scraper execution attempt."""
        from shared.orchestrator import STATE_META

        state_name = STATE_META.get(state_code, {}).get("name", state_code)
        run_type = "manual" if "MANUAL" in run_id or "TEST" in run_id else "scheduled"
        p = self._placeholder()

        insert_sql = f"""
        INSERT INTO scraper_runs
            (run_id, state_code, state_name, status, attempt,
             records_scraped, duration_seconds, error_message, run_type)
        VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p})
        """
        try:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute(insert_sql, (
                run_id, state_code, state_name, status, attempt,
                records_count, round(duration, 2), error_message, run_type,
            ))
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to log run to DB: {e}")

    def log_run_summary(self, summary: dict):
        """Log the overall run summary as a row with state_code='ALL'."""
        total_records = sum(
            r.get("records_scraped", 0) for r in summary.get("details", [])
        )
        overall_status = "success" if summary["failed"] == 0 else "partial"
        run_type = "manual" if "MANUAL" in summary["run_id"] or "TEST" in summary["run_id"] else "scheduled"
        p = self._placeholder()

        insert_sql = f"""
        INSERT INTO scraper_runs
            (run_id, state_code, state_name, status, attempt,
             records_scraped, duration_seconds, error_message, run_type)
        VALUES ({p}, 'ALL', 'Summary', {p}, {p}, {p}, {p}, {p}, {p})
        """
        try:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute(insert_sql, (
                summary["run_id"],
                overall_status,
                summary["total_states"],
                total_records,
                0,
                json.dumps(summary, default=str),
                run_type,
            ))
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to log run summary: {e}")

    def get_recent_runs(self, limit: int = 10) -> list[dict]:
        """Retrieve the most recent run summaries."""
        p = self._placeholder()
        query = f"""
        SELECT run_id, state_code, state_name, status, attempt,
               records_scraped, duration_seconds, error_message,
               run_type, created_at
        FROM scraper_runs
        WHERE state_code = 'ALL'
        ORDER BY created_at DESC
        LIMIT {p}
        """
        try:
            conn = self._get_connection()

            if self.is_sqlite:
                conn.row_factory = lambda c, r: dict(
                    zip([col[0] for col in c.description], r)
                )
                cur = conn.cursor()
                cur.execute(query, (limit,))
            else:
                from psycopg2.extras import RealDictCursor
                cur = conn.cursor(cursor_factory=RealDictCursor)
                cur.execute(query, (limit,))

            rows = cur.fetchall()
            cur.close()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Failed to fetch recent runs: {e}")
            return [{"error": str(e)}]
