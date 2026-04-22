"""
Scraper configuration constants.

All values can be overridden via environment variables.
"""

import os

# ---------------------------------------------------------------------------
# HTTP request settings
# ---------------------------------------------------------------------------
REQUEST_TIMEOUT     = int(os.getenv("REQUEST_TIMEOUT",     "10"))   # seconds for standard page fetches
PDF_REQUEST_TIMEOUT = int(os.getenv("PDF_REQUEST_TIMEOUT", "30"))   # seconds for PDF downloads (larger files)
REQUEST_DELAY       = float(os.getenv("REQUEST_DELAY",     "1.2"))  # polite pause between requests

# ---------------------------------------------------------------------------
# PDF processing
# ---------------------------------------------------------------------------
MIN_PDF_CHARS = int(os.getenv("MIN_PDF_CHARS", "300"))   # PDFs with less text are blank forms — skip them
MAX_PDF_PAGES = int(os.getenv("MAX_PDF_PAGES", "15"))    # only read first N pages to keep memory bounded
AI_TEXT_LIMIT = int(os.getenv("SCRAPER_AI_TEXT_LIMIT", "14000"))  # max chars from page/PDF sent to AI

# ---------------------------------------------------------------------------
# AI call timeout
# ---------------------------------------------------------------------------
AI_CALL_TIMEOUT = int(os.getenv("AI_CALL_TIMEOUT", "30"))  # seconds before giving up on Azure OpenAI

# ---------------------------------------------------------------------------
# Retry settings (used by retry_with_backoff decorator)
# ---------------------------------------------------------------------------
MAX_RETRIES          = int(os.getenv("MAX_RETRIES",          "2"))
RETRY_INITIAL_DELAY  = float(os.getenv("RETRY_INITIAL_DELAY", "1.0"))
RETRY_BACKOFF_FACTOR = float(os.getenv("RETRY_BACKOFF_FACTOR", "2.0"))

# ---------------------------------------------------------------------------
# Concurrency limits
# ---------------------------------------------------------------------------
MAX_AI_CONCURRENT   = int(os.getenv("MAX_CONCURRENT_AI_CALLS", "3"))
MAX_HTTP_CONCURRENT = int(os.getenv("MAX_CONCURRENT_HTTP",     "10"))

# ---------------------------------------------------------------------------
# Deadline year range (mirrors pipeline.constants — scrapers share this logic)
# ---------------------------------------------------------------------------
MIN_YEAR = int(os.getenv("DEADLINE_MIN_YEAR", "2020"))
MAX_YEAR = int(os.getenv("DEADLINE_MAX_YEAR", "2030"))

# ---------------------------------------------------------------------------
# HTTP user agent
# ---------------------------------------------------------------------------
USER_AGENT = os.getenv(
    "SCRAPER_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36",
)
