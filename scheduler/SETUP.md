# Grant Scraper Scheduler — Setup Guide

## Overview

Azure Functions Timer Trigger that runs daily at 5:00 AM EST (10:00 UTC)
to scrape state-level funding opportunities across MD, PA, NY, and DC.
Integrates directly with the existing `gov-opportunities-api` codebase.

## Architecture

```
                    ┌──────────────────────────┐
                    │   Azure Timer Trigger     │
                    │   Cron: 0 0 10 * * *     │
                    │   (5 AM EST daily)        │
                    └────────────┬─────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │   ScraperOrchestrator     │
                    │   Per-state retry (3x)    │
                    │   Skip on failure         │
                    └────────────┬─────────────┘
                                 │
            ┌────────┬───────────┼───────────┬────────┐
            ▼        ▼           ▼           ▼        │
         ┌─────┐ ┌─────┐   ┌─────┐   ┌──────┐       │
         │ MD  │ │ PA  │   │ NY  │   │ DC   │       │
         └──┬──┘ └──┬──┘   └──┬──┘   └──┬───┘       │
            │       │         │         │            │
            └───────┴─────────┴─────────┘            │
                         │                           │
              ┌──────────▼──────────┐                │
              │  Azure GPT-4o       │   enrichment   │
              │  (AI Foundry)       │                │
              └──────────┬──────────┘                │
                         │                           │
              ┌──────────▼──────────┐    ┌───────────▼──────┐
              │  Azure PostgreSQL   │    │  scraper_runs    │
              │  opportunities tbl  │    │  (audit log)     │
              └─────────────────────┘    └──────────────────┘
```

## File Structure

Place this `scheduler/` folder inside your existing repo:

```
gov-opportunities-api/
├── app/                        # existing FastAPI app
├── scrapers/                   # existing state scrapers
│   ├── base_scraper.py
│   ├── md_scraper.py
│   ├── pa_dced_scraper.py
│   ├── ny_scraper.py
│   └── dc_scraper.py
├── pipeline/                   # existing pipeline code
├── scheduler/                  # ← NEW: this folder
│   ├── function_app.py         # timer + HTTP triggers
│   ├── host.json               # function timeout config
│   ├── local.settings.json     # env vars (gitignored)
│   ├── requirements.txt        # Python dependencies
│   └── shared/
│       ├── __init__.py
│       ├── orchestrator.py     # retry logic + state registry
│       └── run_logger.py       # PostgreSQL audit logging
└── run_all_scrapers.py         # existing master runner
```

## Setup Steps

### 1. Prerequisites

Install Azure Functions Core Tools (for local dev/testing):

```bash
# macOS
brew tap azure/functions
brew install azure-functions-core-tools@4

# Windows
winget install Microsoft.Azure.FunctionsCoreTools

# Linux (Ubuntu)
curl https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > packages.microsoft.gpg
sudo install -o root -g root -m 644 packages.microsoft.gpg /etc/apt/trusted.gpg.d/
sudo sh -c 'echo "deb [arch=amd64] https://packages.microsoft.com/repos/microsoft-ubuntu-$(lsb_release -cs)-prod $(lsb_release -cs) main" > /etc/apt/sources.list.d/dotnetdev.list'
sudo apt-get update && sudo apt-get install azure-functions-core-tools-4
```

### 2. Configure Environment

Copy and edit `local.settings.json` with your actual credentials:

```bash
cd scheduler/

# Edit with your Azure PostgreSQL and OpenAI credentials
# These should match what your existing scrapers use
```

Key settings:
- `DB_HOST`: Your Azure PostgreSQL server (same as existing)
- `AZURE_OPENAI_*`: Same credentials your scrapers already use
- `SCRAPER_MAX_RETRIES`: 3 (default)
- `SCRAPER_RETRY_DELAY_SECONDS`: 30 (default, wait between retries)

### 3. Integration (already wired up)

The orchestrator calls your existing `run_all_scrapers.run_all(states=[state_code])`
directly via Python import. No subprocess, no configuration needed.

How it works:
- Timer fires → orchestrator loops through `["MD", "PA", "NY", "DC"]`
- For each state, calls `run_all(states=["PA"])` which runs all sources
  registered under that state code in `ALL_SOURCES`
- If ALL sources for a state fail → retry up to 3 times
- If SOME sources succeed → marks as "partial", moves on
- If all sources succeed → marks as "success"

The only requirement is that `scrapers.run_all_scrapers` is importable
from the scheduler directory. This is handled by the `sys.path` setup
in `run_all_scrapers.py` itself.

### 4. Verify State Sources

The orchestrator reads state codes from `STATE_META` in
`shared/orchestrator.py` (just display names and owners),
then passes them to `run_all(states=[...])` which looks up
actual scraper sources from `ALL_SOURCES` in `run_all_scrapers.py`.

Your current sources per state:
- **MD**: 6 sources (MDOT, MDE, MSDE, MARBIDCO, Commerce, DHCD)
- **PA**: 7 sources (Gov Grants, Grants Search, DLI, DCNR, PennVEST, PEMA, Agriculture)
- **NY**: 7 sources (Empire, DOS, NYSCA, Health, OCFS, NYSED, Homes)
- **DC**: 6 sources (DMPED, DOES, OSSE, Grants Portal, DSLBD, OVSJG)

To add a new source, add it to `ALL_SOURCES` in `run_all_scrapers.py`
— the scheduler picks it up automatically.

### 5. Test Locally

```bash
cd scheduler/

# Install dependencies
pip install -r requirements.txt

# Start the function locally
func start

# The timer won't fire immediately in local mode.
# Use the manual HTTP trigger to test:
curl -X POST http://localhost:7071/api/scraper/run \
  -H "Content-Type: application/json"

# Test a single state:
curl -X POST http://localhost:7071/api/scraper/run \
  -H "Content-Type: application/json" \
  -d '{"states": ["PA"]}'

# Check status:
curl http://localhost:7071/api/scraper/status
```

### 6. Deploy to Azure

```bash
# Login to Azure
az login

# Create the Function App (in your existing resource group)
az functionapp create \
  --resource-group gov-grants-rg-msu \
  --consumption-plan-location eastus2 \
  --runtime python \
  --runtime-version 3.12 \
  --functions-version 4 \
  --name gov-grants-scraper-scheduler \
  --storage-account govgrantsstorage \
  --os-type Linux

# Set environment variables (same DB/AI creds your scrapers use)
az functionapp config appsettings set \
  --resource-group gov-grants-rg-msu \
  --name gov-grants-scraper-scheduler \
  --settings \
    DB_HOST="your-server.postgres.database.azure.com" \
    DB_PORT="5432" \
    DB_NAME="gov_grants" \
    DB_USER="your_user" \
    DB_PASSWORD="your_password" \
    DB_SSLMODE="require" \
    AZURE_OPENAI_ENDPOINT="your_endpoint" \
    AZURE_OPENAI_API_KEY="your_key" \
    AZURE_OPENAI_DEPLOYMENT="gpt-4o" \
    SCRAPER_MAX_RETRIES="3" \
    SCRAPER_RETRY_DELAY_SECONDS="30" \
    SCRAPER_TIMEOUT_SECONDS="600"

# Deploy the function code
cd scheduler/
func azure functionapp publish gov-grants-scraper-scheduler
```

### 7. Verify Deployment

```bash
# Check the function is running
az functionapp function list \
  --resource-group gov-grants-rg-msu \
  --name gov-grants-scraper-scheduler

# Trigger a manual run
curl -X POST \
  "https://gov-grants-scraper-scheduler.azurewebsites.net/api/scraper/run?code=YOUR_FUNCTION_KEY" \
  -H "Content-Type: application/json"

# Check status
curl "https://gov-grants-scraper-scheduler.azurewebsites.net/api/scraper/status"
```

## Monitoring

### Azure Portal

Navigate to your Function App in Azure Portal:
- **Monitor** tab shows invocation history
- **Log stream** shows real-time logs
- **Application Insights** (if enabled) shows detailed telemetry

### Database Audit Trail

Query the `scraper_runs` table directly:

```sql
-- Last 7 days of runs
SELECT run_id, state_code, status, records_scraped,
       duration_seconds, created_at
FROM scraper_runs
WHERE created_at > NOW() - INTERVAL '7 days'
ORDER BY created_at DESC;

-- Failure rate by state
SELECT state_code,
       COUNT(*) FILTER (WHERE status = 'success') AS successes,
       COUNT(*) FILTER (WHERE status = 'failed') AS failures,
       ROUND(AVG(records_scraped) FILTER (WHERE status = 'success')) AS avg_records
FROM scraper_runs
WHERE state_code != 'ALL'
  AND created_at > NOW() - INTERVAL '30 days'
GROUP BY state_code
ORDER BY state_code;

-- Runs with errors (for debugging)
SELECT run_id, state_code, attempt, error_message, created_at
FROM scraper_runs
WHERE status = 'failed'
ORDER BY created_at DESC
LIMIT 20;
```

### Health Check Endpoint

The `/api/scraper/status` endpoint returns recent runs as JSON,
accessible without authentication. Useful for:
- Quick team check before a sponsor meeting
- Runwei frontend integration (if they want scraper health)
- Automated monitoring via cron + curl

## Cost Estimate

Azure Functions Consumption Plan (pay-per-execution):
- 1 execution/day × ~10 min runtime = ~300 min/month
- Free tier: 400,000 GB-s and 1M executions/month
- Your usage: effectively free (well under free tier)

## Adding a New State

When the project expands beyond MD/PA/NY/DC:

1. Create the scraper in `scrapers/` following `base_scraper.py`
2. Add the entry to `STATE_SCRAPERS` in `orchestrator.py`:

```python
"NJ": {
    "name": "New Jersey",
    "module": "scrapers.nj_scraper",
    "class": "NewJerseyScraper",
    "source": "nj.gov/grants",
    "owner": "Team Member Name",
},
```

3. Deploy: `func azure functionapp publish gov-grants-scraper-scheduler`

No changes needed to the timer trigger, retry logic, or logging.
