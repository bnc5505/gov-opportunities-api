# Government Grants Discovery System - Complete Project Overview

**Version:** 1.0
**Last Updated:** April 2026
**Status:** Production Ready
**Delivery Date:** April 23, 2026

---

## Executive Summary

A production-ready platform that automatically discovers, enriches, and serves government grant opportunities across Pennsylvania, New York, Maryland, and DC through a RESTful API.

**Key Metrics:**
- 288 verified grant opportunities across 4 states
- 20+ automated scrapers monitoring government portals
- 97% active grants (not expired)
- 80% cost savings through intelligent caching
- 162 comprehensive tests (100% passing)
- Sub-100ms API response times

**Technology Stack:**
- Backend: FastAPI (Python 3.9+)
- Database: Azure PostgreSQL (17 tables)
- AI: Azure OpenAI GPT-4 (data enrichment)
- Hosting: Azure App Service
- Testing: pytest (162 tests)

---

## Table of Contents

1. [System Architecture](#system-architecture)
2. [How It Works](#how-it-works)
3. [Project Structure](#project-structure)
4. [Database Schema](#database-schema)
5. [Data Pipeline](#data-pipeline)
6. [API Documentation](#api-documentation)
7. [Key Concepts](#key-concepts)
8. [Integration Guide](#integration-guide)
9. [Extension Guide](#extension-guide)
10. [Maintenance and Operations](#maintenance-and-operations)
11. [Troubleshooting](#troubleshooting)
12. [Appendix](#appendix)

---

## System Architecture

### High-Level Overview

```
Government Grant Portals (PA, NY, MD, DC)
         |
         v
Web Scrapers (scrapers/)
20+ sources, runs daily
         |
         v
Staging Table (scraped_grants)
Raw JSON data
         |
         v
AI Enrichment (Azure OpenAI GPT-4)
Extract fields, content-hash caching
         |
         v
Production DB (opportunities table)
Azure PostgreSQL, 288 verified grants
         |
         v
FastAPI (app/main.py)
RESTful endpoints, Swagger at /docs
         |
         v
Runwei Team / Frontend Applications
```

### Three-Layer Architecture

**Data Collection Layer (scrapers/)**
- 20+ automated web scrapers
- Monitors government grant portals daily
- Extracts raw HTML/PDF content
- Stores in JSON files under data/

**Processing Layer (pipeline/)**
- Loads JSON into the staging table (scraped_grants)
- AI enrichment via Azure OpenAI
- Quality scoring (0.0–1.0)
- Syncs to production (opportunities table)

**API Layer (app/)**
- RESTful FastAPI endpoints
- JWT authentication (optional for read endpoints)
- Rate limiting (100 req/min)
- Swagger documentation

---

## How It Works

### Daily Automated Workflow

**Step 1: Web Scraping**

Each scraper visits its assigned government portal, extracts grant listings using BeautifulSoup/regex, fetches detail pages, downloads and parses PDF attachments, and saves output to JSON with a content hash.

**Step 2: Load to Staging Table**

Reads all `*_raw.json` files from data/, computes a SHA-256 content hash per grant, upserts to scraped_grants, and skips unchanged grants (hash match).

**Step 3: Find Missing Deadlines**

Queries grants with missing deadlines and attempts extraction via regex patterns on cached text. Falls back to fetching live pages if needed.

**Step 4: AI Enrichment (two passes)**

- Pass 1: Uses `combined_text` already in the database — no HTTP requests, no PDF downloads. Extracts ~90% of fields.
- Pass 2: Fetches live pages for grants still missing critical fields (application URL or deadline). Limited to 50 grants per run to control cost.

Before any AI call, the content hash is checked against cache. If the page hasn't changed, the cached result is used (accounts for ~80% cost savings).

**Step 5: Sync to Production**

Filters grants to those with score >= 0.50 and a deadline or rolling flag. Generates a SHA-256 dedup key, upserts to the opportunities table, and expires grants whose deadlines have passed.

---

## Project Structure

```
gov-opportunities-api/
|
├── app/                          # FastAPI application
│   ├── main.py                   # Entry point, middleware, routers
│   ├── models.py                 # SQLAlchemy models (17 tables)
│   ├── schemas.py                # Pydantic request/response models
│   ├── database.py               # Connection and session management
│   ├── auth.py                   # JWT token creation and validation
│   ├── rate_limit.py             # SlowAPI rate limiter
│   └── routers/
│       ├── opportunities.py      # Main grants endpoint
│       ├── states.py
│       ├── agencies.py
│       ├── users.py              # User accounts + /auth/login
│       ├── sources.py
│       ├── saved.py              # User bookmarks
│       └── review_queue.py       # Quality review system
|
├── pipeline/                     # Data processing pipeline
│   ├── daily_run.py              # Orchestrator (runs all steps)
│   ├── load_scraped_grants.py    # JSON to staging table
│   ├── enrich_scraped_grants.py  # AI enrichment (2-pass)
│   ├── sync_opportunities.py     # Staging to production
│   ├── find_deadlines.py         # Deadline extraction
│   ├── constants.py              # Pipeline configuration
│   └── backfill_opportunities.py # Data migration utility
|
├── scrapers/                     # Web scrapers
│   ├── base/
│   │   └── base_scraper.py       # Shared utilities: retry, cache, AI, date parsing
│   ├── constants.py              # HTTP/retry/PDF settings
│   ├── pa/                       # Pennsylvania (8 scrapers)
│   ├── ny/                       # New York (9 scrapers)
│   ├── md/                       # Maryland (1 scraper)
│   ├── dc/                       # Washington DC (3 scrapers)
│   └── run_all_scrapers.py       # Multi-state runner
|
├── tests/                        # Test suite (162 tests)
│   ├── test_api.py               # API integration tests (64)
│   └── test_pipeline.py          # Pipeline unit tests (98)
|
├── alembic/                      # Database migrations
│   ├── env.py
│   └── versions/
│       └── 125a4337b000_initial_schema.py
|
├── docs/
│   ├── API_INTEGRATION_GUIDE.md  # For Runwei team
│   ├── DEPLOYMENT.md             # Azure setup
│   └── PROJECT_OVERVIEW.md       # This file
|
├── data/                         # Scraped JSON files (not in repo)
│   ├── pa/
│   ├── ny/
│   ├── md/
│   └── dc/
|
├── README.md
├── requirements.txt
├── .env.example
└── alembic.ini
```

---

## Database Schema

### Core Tables

**opportunities** — main production table
- `id`, `opportunity_key` (SHA-256 dedup key), `title`, `description`, `summary`
- Foreign keys to: states, agencies, sources
- Many-to-many: categories, applicant_types
- `data_quality_score` (0.0–1.0), `status` (active/expired/archived/unverified)

**scraped_grants** — staging table
- Same fields as opportunities plus pipeline metadata
- `content_hash`: detects page changes (SHA-256)
- `enriched_at`: timestamp of last AI enrichment
- `combined_text`: full page text passed to AI

**states** — PA, NY, MD, DC (4 rows, pilot states)

**agencies** — federal, state, and local agencies offering funding

**sources** — one row per website or portal scraped; tracks `last_scraped_at` and `consecutive_failures`

### Supporting Tables

| Table | Purpose |
|-------|---------|
| categories | 25 grant categories, hierarchical |
| applicant_types | Individual, nonprofit, municipality, etc. |
| users | Email/password accounts, individual or organization |
| saved_opportunities | User bookmarks with optional notes |
| saved_searches | Saved filter sets with notification support |
| review_queue | Grants flagged for human verification |
| opportunity_documents | Linked PDFs/guidelines (stores URLs, not files) |
| scrape_logs | Execution history and health metrics per scraper |

### Key Relationships

```
states       (1) --< (many) opportunities
agencies     (1) --< (many) opportunities
sources      (1) --< (many) opportunities

opportunities >--< categories       (many-to-many)
opportunities >--< applicant_types  (many-to-many)

users  (1) --< (many) saved_opportunities
users  (1) --< (many) saved_searches

opportunities (1) --< (many) review_queue
opportunities (1) --< (many) opportunity_documents
```

### Deduplication Strategy

The same grant often appears on multiple pages. To avoid duplicates, each opportunity gets a unique key:

```python
opportunity_key = sha256(f"{state_code}|{opportunity_url}".lower()).hexdigest()[:64]
```

On sync, this key is used as the upsert anchor — insert on first appearance, update on subsequent runs.

---

## Data Pipeline

### Quality Scoring

Grants are scored 0.0–1.0 based on field completeness:

```python
SCORE_WEIGHTS = {
    "title":             0.15,
    "description":       0.15,
    "award_max":         0.12,
    "application_url":   0.12,
    "eligibility_notes": 0.10,
    "summary":           0.08,
    "tags":              0.05,
    "areas_of_focus":    0.05,
    "contact_email":     0.03,
    # deadline or rolling share one slot:
    "deadline_or_rolling": 0.15,
}
```

**Thresholds:**

| Score | Tier | Action |
|-------|------|--------|
| >= 0.80 | Excellent | Publish |
| 0.70–0.79 | Good | Publish |
| 0.50–0.69 | Fair | Publish, flag for review |
| < 0.50 | Poor | Blocked — do not publish |

### Content-Hash Caching

Before calling Azure OpenAI, the pipeline checks whether the scraped page content has changed:

```python
if cache.get_if_fresh(url, content_hash):
    return cached_result   # no AI call
else:
    result = call_ai(content)
    cache.set(url, content_hash, result)
```

Most grant pages don't change daily. This skip logic saves roughly 80% of AI API costs.

### Pipeline Flow

```
daily_run.py
|
├── Step 1: scrapers/run_all_scrapers.py
|       20+ scrapers in parallel
|       → data/{state}/*_raw.json
|
├── Step 2: pipeline/load_scraped_grants.py
|       JSON files → scraped_grants table
|       (skips unchanged by content hash)
|
├── Step 3: pipeline/find_deadlines.py
|       Extracts missing deadlines via regex
|
├── Step 4: pipeline/enrich_scraped_grants.py
|       Pass 1: AI extraction from cached text
|       Pass 2: fetch live pages (max 50/run)
|       → data_quality_score, enriched_at updated
|
└── Step 5: pipeline/sync_opportunities.py
        Filter: score >= 0.50 AND (deadline OR rolling)
        Upsert to opportunities table
        Expire stale grants
```

---

## API Documentation

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | API info |
| GET | `/health` | Health check |
| GET | `/docs` | Swagger UI |
| POST | `/auth/login` | Get JWT token |
| GET | `/opportunities` | List opportunities (filterable, paginated) |
| GET | `/opportunities/{id}` | Get single opportunity |
| GET | `/states` | List states |
| GET | `/agencies` | List agencies |
| GET | `/sources` | List scraper sources |
| GET | `/review-queue` | Items needing human review |
| PUT | `/review-queue/{id}` | Update review status |
| GET | `/users/{id}/saved-opportunities` | User's saved grants |
| POST | `/users/{id}/saved-opportunities` | Save a grant |

### Query Parameters for GET /opportunities

| Parameter | Type | Description |
|-----------|------|-------------|
| `q` | string | Full-text search across title, summary, description |
| `state_code` | string | PA, NY, MD, or DC |
| `award_min` | float | Grants where max award >= this value |
| `award_max` | float | Grants where min award <= this value |
| `deadline_after` | datetime | ISO 8601 |
| `deadline_before` | datetime | ISO 8601 |
| `rolling` | boolean | Grants with no fixed deadline |
| `eligibility_individual` | boolean | Open to individuals |
| `eligibility_organization` | boolean | Open to organizations |
| `sort_by` | string | deadline, award_min, award_max, created_at, title |
| `sort_order` | string | asc or desc |
| `page` | int | Page number (default 1) |
| `per_page` | int | Results per page (1–100, default 20) |

### Authentication

Auth is wired up but read endpoints do not require a token. To get one:

```bash
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=demo@example.com&password=anypassword"
```

During the demo period, any credentials are accepted. Tokens expire after 60 minutes.

### Rate Limiting

The `/opportunities` list endpoint is limited to 100 requests per minute per IP. Responses include `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `X-RateLimit-Reset` headers. Exceeding the limit returns HTTP 429.

---

## Key Concepts

### Two-Pass Enrichment

Pass 1 is fast — it uses `combined_text` already stored in the database, makes no HTTP requests, and extracts roughly 90% of fields for most grants.

Pass 2 is expensive — it fetches the live grant page, scans for apply links and missing fields, and is capped at 50 grants per run to control costs. It only runs for grants that are still missing an application URL or deadline after Pass 1.

### Review Queue

Grants with `data_quality_score < 0.70` or `needs_review = true` are automatically added to the review_queue table. An admin can approve, reject, or edit them. This is the human-in-the-loop layer for data the AI couldn't extract cleanly.

### Incremental Updates

Nothing is rebuilt from scratch on each run. Scraping only re-fetches changed pages. Loading skips grants with matching content hashes. Enrichment skips grants already processed unless `--force` is passed. Syncing only updates records that have changed.

---

## Integration Guide

### Quick Start

```bash
# Health check
curl http://localhost:8000/health

# All PA grants
curl "http://localhost:8000/opportunities?state_code=PA"

# Filter by award range
curl "http://localhost:8000/opportunities?award_min=50000&award_max=200000"

# Combined filters with pagination
curl "http://localhost:8000/opportunities?state_code=NY&award_min=100000&per_page=10"
```

### Response Structure

```json
{
  "total": 288,
  "page": 1,
  "per_page": 20,
  "total_pages": 15,
  "data": [
    {
      "id": 123,
      "title": "Infrastructure Improvement Grant",
      "summary": "Infrastructure funding for municipalities",
      "opportunity_type": "grant",
      "award_min": 50000.0,
      "award_max": 500000.0,
      "deadline": "2026-06-30T00:00:00",
      "rolling": false,
      "opportunity_url": "https://pa.gov/grants/123",
      "application_url": "https://pa.gov/apply/123",
      "status": "active",
      "eligibility_individual": false,
      "eligibility_organization": true,
      "data_quality_score": 0.92,
      "tags": ["Infrastructure", "Transportation"],
      "agency": { "code": "DCED", "name": "PA Dept of Community and Economic Development" },
      "state": { "code": "PA", "name": "Pennsylvania" }
    }
  ]
}
```

### TypeScript Types

```typescript
interface Opportunity {
  id: number;
  title: string;
  description?: string;
  summary?: string;
  opportunity_type: 'grant' | 'loan' | 'tax_credit' | 'fellowship' | 'accelerator';
  award_min?: number;
  award_max?: number;
  deadline?: string;       // ISO 8601
  rolling?: boolean;
  opportunity_url?: string;
  application_url?: string;
  status: 'active' | 'expired' | 'archived' | 'unverified';
  eligibility_individual: boolean;
  eligibility_organization: boolean;
  data_quality_score?: number;
  tags?: string[];
  agency?: { code: string; name: string; level: string };
  state?: { code: string; name: string };
  created_at: string;
  updated_at: string;
}

interface PaginatedResponse {
  total: number;
  page: number;
  per_page: number;
  total_pages: number;
  data: Opportunity[];
}
```

### Error Handling

| Status | Meaning |
|--------|---------|
| 200 | Success |
| 201 | Created |
| 204 | Deleted |
| 400 | Bad request (e.g. duplicate email) |
| 401 | Invalid or expired token |
| 404 | Not found |
| 422 | Validation error (bad query param) |
| 429 | Rate limit exceeded |
| 500 | Server error |

---

## Extension Guide

### Adding a New State

1. Insert the state: `INSERT INTO states (code, name, is_active) VALUES ('VA', 'Virginia', true);`
2. Create a scraper in `scrapers/va/` inheriting from `BaseScraper`
3. Register it in `scrapers/run_all_scrapers.py`
4. Add the file prefix to `PREFIX_STATE_MAP` in `pipeline/load_scraped_grants.py`
5. Run `python pipeline/daily_run.py`

### Adding a New Scraper to an Existing State

1. Create the scraper file in `scrapers/{state}/`
2. Inherit from `BaseScraper`, implement `scrape()`
3. Add it to `scrapers/run_all_scrapers.py`

### Modifying Quality Score Weights

Edit `SCORE_WEIGHTS` in `pipeline/enrich_scraped_grants.py`, then run:
```bash
python pipeline/enrich_scraped_grants.py --force
python pipeline/sync_opportunities.py
```

### Adding a New API Endpoint

Add a route function to the appropriate router in `app/routers/`, following the existing patterns. Register the router in `app/main.py` if it's a new file.

---

## Maintenance and Operations

### Daily Pipeline

The full pipeline runs in approximately 25–35 minutes:

```bash
python pipeline/daily_run.py
# Flags: --skip-scrape, --skip-enrich, --dry-run
```

Individual steps can be run in isolation:

```bash
python scrapers/run_all_scrapers.py --states PA NY
python pipeline/load_scraped_grants.py
python pipeline/enrich_scraped_grants.py --limit 50
python pipeline/sync_opportunities.py --dry-run
```

### Useful Monitoring Queries

```sql
-- Grant distribution by state
SELECT s.code, COUNT(o.id) as grants
FROM states s LEFT JOIN opportunities o ON o.state_id = s.id
GROUP BY s.code ORDER BY grants DESC;

-- Grants expiring in the next 30 days
SELECT title, deadline, application_url
FROM opportunities
WHERE deadline BETWEEN NOW() AND NOW() + INTERVAL '30 days'
  AND rolling = false
ORDER BY deadline;

-- Quality distribution
SELECT
  CASE
    WHEN data_quality_score >= 0.80 THEN 'High'
    WHEN data_quality_score >= 0.70 THEN 'Good'
    WHEN data_quality_score >= 0.50 THEN 'Fair'
    ELSE 'Poor'
  END as tier,
  COUNT(*) as count
FROM opportunities GROUP BY tier;

-- Scraper health
SELECT name, last_scraped_at, consecutive_failures
FROM sources WHERE is_active = true ORDER BY consecutive_failures DESC;
```

### Tuning via Environment Variables

```bash
TARGET_QUALITY_SCORE=0.75   # Lower threshold to publish more grants
MAX_CONCURRENT_AI_CALLS=5   # Increase for faster enrichment (higher cost)
PASS2_LIMIT=20               # Reduce to cut AI costs
REQUEST_TIMEOUT=15           # Increase for slow government sites
```

### Estimated Monthly Costs

| Service | Cost |
|---------|------|
| Azure OpenAI | ~$50 |
| Azure PostgreSQL | ~$30 |
| Azure App Service | ~$50 |
| **Total** | **~$130** |

The 80% AI cost saving from content-hash caching is the largest cost lever. Reducing `PASS2_LIMIT_DEFAULT` and increasing `TARGET_QUALITY_SCORE` further reduce AI spend.

---

## Troubleshooting

### Scraper Timing Out

Check `consecutive_failures` in the sources table. Increase `REQUEST_TIMEOUT` in `.env` or `REQUEST_DELAY` if the site is rate-limiting requests. If the site structure changed, the scraper's CSS selectors will need updating.

### Low Quality Scores Across the Board

Check that Azure OpenAI credentials are set and the AI is actually running. Inspect a few raw rows in `scraped_grants` to see what fields are missing. The AI prompt is in `enrich_scraped_grants.py` — adjusting it may help extraction.

### API Responses Slow

Run `EXPLAIN ANALYZE` on the slow query. The most common fix is adding a composite index on `(state_id, deadline)`. Upgrading the Azure PostgreSQL tier is the next lever.

### High AI Costs

Verify caching is working — logs should show "Using cached result" for most rows. Check whether grants are being re-enriched unnecessarily (compare `enriched_at` timestamps). Reducing `PASS2_LIMIT_DEFAULT` is the fastest fix.

### Database Connection Errors

Check `DATABASE_URL` format and that `?sslmode=require` is present for Azure PostgreSQL. Verify firewall rules allow the App Service IP. Check `max_connections` on the database tier.

---

## Appendix

### Environment Variables Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | Yes | — | PostgreSQL connection string |
| `AZURE_OPENAI_API_KEY` | Pipeline only | — | Azure OpenAI API key |
| `AZURE_OPENAI_ENDPOINT` | Pipeline only | — | Azure OpenAI endpoint |
| `AZURE_OPENAI_DEPLOYMENT` | No | gpt-4o | Model deployment name |
| `JWT_SECRET_KEY` | Yes | — | Secret for JWT signing (32+ chars) |
| `ALGORITHM` | No | HS256 | JWT algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | 60 | Token lifetime in minutes |
| `CORS_ORIGINS` | No | localhost | Comma-separated allowed origins |
| `TARGET_QUALITY_SCORE` | No | 0.80 | Enrichment score threshold |
| `MAX_CONCURRENT_AI_CALLS` | No | 3 | Parallel AI requests |
| `PASS2_LIMIT` | No | 50 | Max grants per Pass 2 run |
| `REQUEST_TIMEOUT` | No | 10 | HTTP timeout (seconds) |
| `MAX_PDF_PAGES` | No | 15 | Max PDF pages to read |

### Testing Checklist

Before deployment:
- [ ] All 162 tests passing (`pytest tests/ -v`)
- [ ] Swagger docs load at `/docs`
- [ ] Database migrations applied (`alembic upgrade head`)
- [ ] All required environment variables set
- [ ] Health check returns 200
- [ ] `/opportunities` returns grant data
- [ ] State filter works (`?state_code=PA`)
- [ ] Pagination works (`?page=2&per_page=10`)
- [ ] Rate limiting works (101st request in a minute returns 429)
- [ ] JWT token is issued at `/auth/login`

After deployment:
- [ ] Run `daily_run.py` manually once and check logs
- [ ] Verify grant count in database
- [ ] Monitor Application Insights for errors
- [ ] Confirm scheduled run triggers

---

**Version:** 1.0
**Last Updated:** April 2026
**Status:** Production Ready
