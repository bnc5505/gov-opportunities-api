# Setting Up the Scheduler

The scheduler is an Azure Function that runs the full scraping pipeline every day at 5 AM Eastern time. Once it is deployed, you do not need to do anything manually. It wakes up, runs all four states, and goes back to sleep. This document explains how to get it running.

---

## What it does

Every morning the function triggers automatically, loops through Maryland, Pennsylvania, New York, and DC, and calls the same scrapers that you would run by hand. If a state fails it tries again up to three times before moving on. Everything it does gets logged to the database so you can look back at any run later.

There is also a manual trigger if you ever need to kick off a run outside the schedule, and a status endpoint that shows the last ten runs as JSON.

---

## Before you start

You need two things installed on your machine.

Azure CLI, which handles authentication and deployment to your Azure account.

```
brew install azure-cli
```

Azure Functions Core Tools, which lets you run the function locally before deploying.

```
brew tap azure/functions
brew install azure-functions-core-tools@4
```

---

## Test it locally first

This step is worth doing before you deploy. It catches configuration problems early.

Go into the scheduler folder and install its dependencies.

```
cd scheduler
pip install -r requirements.txt
```

Start the function.

```
func start
```

The timer will not fire automatically in local mode since it is set to 5 AM UTC. Use the manual trigger instead to confirm everything is wired up correctly.

```
curl -X POST http://localhost:7071/api/scraper/run \
  -H "Content-Type: application/json" \
  -d '{"states": ["PA"]}'
```

If that returns a result with grant counts, the function is working. Run all states if you want to be thorough.

```
curl -X POST http://localhost:7071/api/scraper/run \
  -H "Content-Type: application/json"
```

---

## Deploy to Azure

Log in first.

```
az login
```

Create the Function App. You only do this once. Use the same resource group as the rest of the project.

```
az functionapp create \
  --resource-group gov-grants-rg-msu \
  --consumption-plan-location eastus2 \
  --runtime python \
  --runtime-version 3.12 \
  --functions-version 4 \
  --name gov-grants-scraper-scheduler \
  --storage-account govgrantsstorage \
  --os-type Linux
```

Set the environment variables. These are the same credentials your scrapers already use locally, just moved into Azure Application Settings.

```
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
    AZURE_OPENAI_DEPLOYMENT="gpt-4o"
```

Deploy the code.

```
cd scheduler
func azure functionapp publish gov-grants-scraper-scheduler
```

After publishing, the CLI will print a function key. Save it. You need it to call the manual trigger in production.

---

## Trigger a manual run in production

```
curl -X POST \
  "https://gov-grants-scraper-scheduler.azurewebsites.net/api/scraper/run?code=YOUR_FUNCTION_KEY" \
  -H "Content-Type: application/json"
```

To run just one state pass it in the body.

```
curl -X POST \
  "https://gov-grants-scraper-scheduler.azurewebsites.net/api/scraper/run?code=YOUR_FUNCTION_KEY" \
  -H "Content-Type: application/json" \
  -d '{"states": ["NY"]}'
```

---

## Check recent runs

This endpoint does not require a key. It returns the last ten runs with their status and record counts.

```
curl https://gov-grants-scraper-scheduler.azurewebsites.net/api/scraper/status
```

You can also query the database directly if you want more detail.

```sql
select run_id, state_code, status, records_scraped, duration_seconds, created_at
from scraper_runs
order by created_at desc
limit 20;
```

---

## If something goes wrong

Check the live log stream in Azure Portal. Go to your Function App, open the Monitor tab, and look at the most recent invocation. The error will be there with a full traceback.

The most common issues are wrong database credentials, the OpenAI endpoint being unreachable, or the function timing out. The timeout is set to 9 minutes and 30 seconds in host.json. If your full run takes longer than that, increase it or split the states across separate functions.

---

## Adding a new state

When the project expands to a new state, create the scrapers under the new state folder following the same pattern as the existing ones, register them in run_all_scrapers.py, and add the state to STATE_META in scheduler/shared/orchestrator.py with a name and an owner. The timer and retry logic pick it up automatically on the next run. No other changes needed.
