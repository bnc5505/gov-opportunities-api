"""
Pipeline configuration constants.

All values can be overridden via environment variables so staging/production
environments can tune thresholds without code changes.
"""

import os

# ---------------------------------------------------------------------------
# Quality score thresholds
# ---------------------------------------------------------------------------
MIN_SCORE           = float(os.getenv("MIN_QUALITY_SCORE",    "0.50"))  # absolute floor
HIGH_SCORE          = float(os.getenv("HIGH_QUALITY_SCORE",   "0.70"))  # above this = high quality
REVIEW_BELOW        = float(os.getenv("REVIEW_BELOW_SCORE",   "0.70"))  # below this = needs review
TARGET_SCORE        = float(os.getenv("TARGET_QUALITY_SCORE", "0.80"))  # enrichment goal

# ---------------------------------------------------------------------------
# Award thresholds
# ---------------------------------------------------------------------------
MIN_AWARD_THRESHOLD = int(os.getenv("MIN_AWARD_THRESHOLD", "5000"))  # lower-scored grants need at least this

# ---------------------------------------------------------------------------
# AI enrichment limits
# ---------------------------------------------------------------------------
AI_TEXT_LIMIT       = int(os.getenv("AI_TEXT_LIMIT",   "8000"))  # max chars sent per enrichment call
AI_CALL_TIMEOUT     = int(os.getenv("AI_CALL_TIMEOUT", "30"))    # seconds before giving up on AI call
PASS2_LIMIT_DEFAULT = int(os.getenv("PASS2_LIMIT",     "50"))    # max grants sent to Pass 2 per run

# ---------------------------------------------------------------------------
# Deadline year range (dates outside this window are treated as parse errors)
# ---------------------------------------------------------------------------
MIN_YEAR = int(os.getenv("DEADLINE_MIN_YEAR", "2020"))
MAX_YEAR = int(os.getenv("DEADLINE_MAX_YEAR", "2030"))

# ---------------------------------------------------------------------------
# Concurrency limits
# ---------------------------------------------------------------------------
MAX_AI_CONCURRENT   = int(os.getenv("MAX_CONCURRENT_AI_CALLS", "3"))
MAX_HTTP_CONCURRENT = int(os.getenv("MAX_CONCURRENT_HTTP",     "10"))
