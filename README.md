Government Grants Discovery API

This project is a backend system that automatically finds and organizes government grant opportunities across four pilot states: Pennsylvania, New York, Maryland, and Washington D.C. The goal is to make it easier for individuals, nonprofits, and small businesses to discover funding they are eligible for, without having to manually search dozens of government websites.

We built this as a RESTful API that a frontend application can connect to, and the entire data pipeline from scraping to delivery runs automatically.


What the Project Does

Most grant information is scattered across hundreds of government websites, buried in PDFs, or listed in formats that are hard to search. Our system solves this by:

1. Scraping over 22 government websites across PA, NY, MD, and DC for active grant listings
2. Cleaning and scoring each record for quality before anything goes into the database
3. Exposing the cleaned data through a REST API with powerful search and filtering
4. Flagging low-confidence records for human review before they go live to users


How It Works

The system has three layers.

Layer 1 - Scrapers (scrapers/ folder)

We have custom scrapers for 22+ sources. Each scraper visits a government website, extracts grant information (title, deadline, award amount, eligibility, contact info), and saves it as a JSON file. The scrapers use a shared base class that handles rate limiting, PDF extraction, and date parsing. All scraped JSON files land in the data/ folder organized by state.

Run all scrapers:
    python -m scrapers.run_all_scrapers

Run scrapers for a specific state:
    python -m scrapers.run_all_scrapers --state PA

Layer 2 - Pipeline (pipeline/ folder)

The pipeline takes the raw scraped JSON and processes it into the database in steps:

Load step: Reads all JSON files, filters out navigation links and non-grant pages, deduplicates records, and loads everything into a staging table called scraped_grants.

    python pipeline/load_scraped_grants.py

Sync step: Takes records from scraped_grants that meet our quality bar and upserts them into the main opportunities table. Our quality bar requires at minimum a title, an application URL, a deadline or rolling status, and either a quality score above 0.70 or a minimum award amount of $5,000.

    python pipeline/sync_opportunities.py

Full pipeline at once:

    python pipeline/daily_run.py

Layer 3 - API (app/ folder)

A FastAPI application that exposes the grant data through REST endpoints. The API supports searching by state, category, award range, deadline, and eligibility type. It also includes a review queue for records that need human verification.

Start the API locally:

    uvicorn app.main:app --reload --app-dir app

Interactive API documentation is at http://localhost:8000/docs once the server is running.


Project Structure

    gov-opportunities-api/
    |-- app/               FastAPI application (models, schemas, routers, database)
    |-- scrapers/          All scraper code organized by state
    |   |-- base/          Shared utilities (HTTP, PDF extraction, date parsing, scoring)
    |   |-- pa/            Pennsylvania scrapers
    |   |-- ny/            New York scrapers
    |   |-- md/            Maryland scrapers
    |   |-- dc/            Washington D.C. scrapers
    |-- pipeline/          ETL scripts (load, sync, enrich, daily runner)
    |-- data/              Scraped JSON output organized by state
    |-- requirements.txt   Python dependencies


How to Set Up

Prerequisites: Python 3.9+

1. Clone the repository and create a virtual environment:

    python -m venv .venv
    source .venv/bin/activate         (Mac/Linux)
    .venv\Scripts\activate            (Windows)

2. Install dependencies:

    pip install -r requirements.txt

3. Copy the environment file and fill in your values:

    cp .env.example .env

4. The database is SQLite for local development and is created automatically on first run. No setup needed.

5. Start the API:

    uvicorn app.main:app --reload --app-dir app

6. To populate the database with grant data, run the pipeline:

    python pipeline/load_scraped_grants.py
    python pipeline/sync_opportunities.py

The database currently contains 290 verified, active grant opportunities across PA, NY, MD, and DC.


Environment Variables (.env file)

    DATABASE_URL             SQLite path for local dev (defaults to app/gov_grants.db)
    CORS_ORIGINS             Comma-separated list of allowed frontend origins
    AZURE_OPENAI_ENDPOINT    Azure OpenAI endpoint for AI enrichment (optional)
    AZURE_OPENAI_KEY         Azure OpenAI API key (optional)
    KEY_VAULT_NAME           Azure Key Vault name for production credentials (optional)

If Azure credentials are not provided, the AI enrichment step is skipped and the rest of the pipeline runs fine without it.


API Endpoints

    GET  /opportunities              List and search grants (13 filter parameters)
    GET  /opportunities/{id}         Get a single grant by ID
    POST /opportunities              Add a grant manually
    PUT  /opportunities/{id}         Update a grant
    DELETE /opportunities/{id}       Delete a grant

    GET  /states                     List pilot states
    GET  /agencies                   List agencies
    GET  /sources                    List scraper source websites

    GET  /review-queue               Review queue for records needing human check
    PUT  /review-queue/{id}          Mark a record as approved, rejected, or needs edit

    POST /users                      Register a user account
    GET  /users/{id}                 Get a user profile

    POST /saved                      Save a grant to a user's bookmark list
    GET  /saved?user_id={id}         Get a user's saved grants
    DELETE /saved/{id}               Remove a saved grant

Example search:

    GET /opportunities?state=NY&rolling=true&award_min=5000&per_page=20


How We Score Data Quality

Every scraped record gets a data_quality_score between 0 and 1.0 before it touches the database. The score is calculated from how complete the record is: title, description, deadline, award amount, eligibility information, and contact details all contribute points.

We only move a record from the staging table to the live opportunities table if:
- It has a title and a working application URL
- It has a deadline date or is marked as rolling (always accepting applications)
- It has a quality score above 0.70, OR it has a minimum award amount of at least $5,000
- Its status is active, rolling, or expiring soon

Records below 0.70 that still qualify go into the review_queue table so a human can verify them before they appear in search results.


Technology Stack

- Python 3.9
- FastAPI 0.115 for the REST API
- SQLAlchemy 2.0 ORM with SQLite locally and Azure PostgreSQL in production
- Pydantic v2 for request and response validation
- BeautifulSoup4 for HTML scraping
- PyPDF2 for reading grant details from PDF documents
- Azure OpenAI (optional) for enriching incomplete records
- Azure Key Vault for secrets management in production


Current Status

The API is fully functional locally with 290 active grant opportunities loaded from PA, NY, MD, and DC. All scrapers are working across all four states. The full pipeline runs from scrape to API without manual steps. Azure Key Vault access is configured for the production deployment.

What is left for the next phase:
- JWT authentication for protected endpoints
- A frontend application to connect to this API
- Automated daily pipeline scheduling
- Docker container configuration for deployment


For any questions about running the project, start the server and visit http://localhost:8000/docs for full interactive API documentation.
