# Government Grants Discovery API

A production-ready API for discovering government grant opportunities across Pennsylvania, New York, Maryland, and DC. Features automated web scraping, AI-powered data enrichment, quality scoring, and RESTful access to 288+ verified grant opportunities.

- 288 verified grant opportunities
- 4 states (PA, NY, MD, DC)
- 97% active grants
- 162 tests passing
- JWT authentication ready
- Sub-2s test execution

## Features

- **Multi-State Coverage**: Automated scrapers for 20+ government grant portals
- **AI Enrichment**: Azure OpenAI extracts structured data from unstructured sources
- **Quality Scoring**: 0.0–1.0 weighted algorithm filters low-quality listings
- **Smart Caching**: Content-hash deduplication saves 80% on AI costs
- **Review Queue**: Human-in-the-loop verification for borderline grants
- **RESTful API**: Filter by state, award amount, deadline, eligibility
- **Production Database**: Azure PostgreSQL with Alembic migrations
- **Security**: JWT authentication, input validation, rate limiting (100/min)

## Tech Stack

- **Backend**: FastAPI (Python 3.9+)
- **Database**: PostgreSQL 12+ (Azure PostgreSQL in production)
- **AI**: Azure OpenAI (GPT-4)
- **ORM**: SQLAlchemy + Alembic migrations
- **Testing**: pytest (162 tests, 100% pass rate)
- **Authentication**: JWT (python-jose)
- **Rate Limiting**: SlowAPI

## Prerequisites

- Python 3.9 or higher
- PostgreSQL 12+ (local or Azure)
- Azure OpenAI API key (for enrichment pipeline)
- Git

## Quick Start

### 1. Clone and Setup

```bash
git clone <repository-url>
cd gov-opportunities-api

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
# Copy example environment file
cp .env.example .env

# Edit .env and set these critical variables:
# - DATABASE_URL (your PostgreSQL connection string)
# - AZURE_OPENAI_API_KEY (for enrichment)
# - JWT_SECRET_KEY (generate with: openssl rand -hex 32)
```

### 3. Initialize Database

```bash
# Run migrations
alembic upgrade head

# Seed initial data (states)
python -c "from app.database import seed_states; seed_states()"
```

### 4. Start the API

```bash
# From project root
cd app
uvicorn main:app --reload

# API available at: http://localhost:8000
# Swagger docs at:  http://localhost:8000/docs
```

### 5. Test the API

```bash
# Get all opportunities
curl http://localhost:8000/opportunities?per_page=5

# Filter by state
curl http://localhost:8000/opportunities?state_code=PA

# Filter by award amount
curl http://localhost:8000/opportunities?award_min=50000
```

## Running the Data Pipeline

### Manual Run

```bash
# Full pipeline (scrape → enrich → sync)
python pipeline/daily_run.py

# Skip scraping (use existing data)
python pipeline/daily_run.py --skip-scrape

# Skip enrichment (no AI costs)
python pipeline/daily_run.py --skip-enrich
```

### Individual Steps

```bash
# 1. Scrape grants from sources
python scrapers/run_all_scrapers.py

# 2. Load scraped JSON into database
python pipeline/load_scraped_grants.py

# 3. AI enrichment (uses Azure OpenAI)
python pipeline/enrich_scraped_grants.py

# 4. Sync to opportunities table
python pipeline/sync_opportunities.py
```

## Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=app --cov=pipeline

# Run specific test file
pytest tests/test_api.py -v

# Run fast (skip slow tests)
pytest -m "not slow"
```

## Project Structure

```
gov-opportunities-api/
├── app/                      # FastAPI application
│   ├── main.py              # Application entry point
│   ├── models.py            # SQLAlchemy models
│   ├── schemas.py           # Pydantic schemas
│   ├── database.py          # Database connection
│   ├── auth.py              # JWT authentication
│   ├── rate_limit.py        # Rate limiting config
│   └── routers/             # API route handlers
│       ├── opportunities.py
│       ├── states.py
│       ├── agencies.py
│       └── users.py
├── pipeline/                 # Data processing pipeline
│   ├── daily_run.py         # Orchestrator
│   ├── load_scraped_grants.py
│   ├── enrich_scraped_grants.py
│   ├── sync_opportunities.py
│   └── find_deadlines.py
├── scrapers/                 # Web scrapers
│   ├── base/                # Base scraper class
│   ├── pa/                  # Pennsylvania scrapers
│   ├── ny/                  # New York scrapers
│   ├── md/                  # Maryland scrapers
│   └── dc/                  # DC scrapers
├── alembic/                  # Database migrations
│   └── versions/
├── tests/                    # Test suite (162 tests)
│   ├── test_api.py
│   └── test_pipeline.py
├── requirements.txt
├── .env.example
└── README.md
```

## Environment Variables

| Variable | Required | Description | Default |
|----------|----------|-------------|---------|
| `DATABASE_URL` | Yes | PostgreSQL connection string | — |
| `AZURE_OPENAI_API_KEY` | Yes* | Azure OpenAI API key | — |
| `AZURE_OPENAI_ENDPOINT` | Yes* | Azure OpenAI endpoint URL | — |
| `AZURE_OPENAI_DEPLOYMENT` | No | Model deployment name | `gpt-4o` |
| `JWT_SECRET_KEY` | Yes | Secret for JWT signing (32+ chars) | — |
| `ALGORITHM` | No | JWT algorithm | `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | Token expiration time | `60` |
| `CORS_ORIGINS` | No | Allowed CORS origins (comma-separated) | `http://localhost:3000` |
| `RATE_LIMIT_PER_MINUTE` | No | API rate limit per IP | `100` |

*Required for enrichment pipeline only

## Authentication

### Get JWT Token

```bash
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=demo@example.com&password=anypassword"

# Returns:
# {"access_token": "eyJ...", "token_type": "bearer"}
```

### Use Token (Optional)

```bash
curl http://localhost:8000/opportunities \
  -H "Authorization: Bearer YOUR_TOKEN_HERE"
```

**Note**: Authentication is currently optional for read endpoints (configured for demo access).

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | API info |
| `/health` | GET | Health check |
| `/docs` | GET | Swagger UI |
| `/opportunities` | GET | List opportunities (filterable, paginated) |
| `/opportunities/{id}` | GET | Get single opportunity |
| `/states` | GET | List states |
| `/agencies` | GET | List agencies |
| `/auth/login` | POST | Get JWT token |

Full API documentation: http://localhost:8000/docs

##  Performance

- **API Response**: <100ms (typical)
- **Test Suite**: 162 tests in ~2s
- **Pipeline Runtime**: ~30 min (full scrape + enrich)
- **Database Size**: 288 opportunities, ~17 tables
- **Cost Optimization**: 80% reduction via content caching

## Contributing

### Adding a New State Scraper

1. Create scraper in `scrapers/<state>/`
2. Inherit from `BaseScraper`
3. Implement `scrape()` method
4. Add to `scrapers/run_all_scrapers.py`
5. Add state to database via Alembic migration

### Running Development Server

```bash
# With auto-reload
cd app
uvicorn main:app --reload --port 8000

# With debug logging
uvicorn main:app --reload --log-level debug
```



## Acknowledgments

- State grant portals for open data access
- Azure OpenAI for data enrichment
- FastAPI framework

## Support

For issues or questions:
- Check `/docs` for API documentation
- Review `API_INTEGRATION_GUIDE.md` for integration help
- See `DEPLOYMENT.md` for production deployment

---


