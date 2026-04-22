# Deployment Guide

Target Platform: Microsoft Azure
Database: Azure PostgreSQL Flexible Server
API Hosting: Azure App Service (Python 3.9)
Status: Production-ready deployment guide

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Azure PostgreSQL Setup](#azure-postgresql-setup)
3. [Database Migration](#database-migration)
4. [Azure App Service Setup](#azure-app-service-setup)
5. [Environment Variables](#environment-variables)
6. [SSL Configuration](#ssl-configuration)
7. [CI/CD with GitHub Actions](#cicd-with-github-actions)
8. [Monitoring and Logging](#monitoring-and-logging)
9. [Production Checklist](#production-checklist)
10. [Troubleshooting](#troubleshooting)

---

## Prerequisites

Before deploying, make sure you have:

- Azure account with an active subscription
- Azure CLI installed (`az --version`)
- GitHub repository with the code
- Azure OpenAI resource (only needed for the enrichment pipeline)
- A domain name (optional, for custom domain)

**Install Azure CLI:**

```bash
# macOS
brew install azure-cli

# Windows
winget install Microsoft.AzureCLI

# Linux
curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash
```

**Log in:**

```bash
az login
az account set --subscription "YOUR_SUBSCRIPTION_NAME"
```

---

## Azure PostgreSQL Setup

### 1. Create a resource group

```bash
REGION="eastus"
RESOURCE_GROUP="gov-grants-rg"

az group create \
  --name $RESOURCE_GROUP \
  --location $REGION
```

Pick a region close to your users. eastus works fine if you are on the East Coast.

### 2. Create the PostgreSQL server

```bash
DB_SERVER="gov-grants-db"
ADMIN_USER="dbadmin"
ADMIN_PASSWORD="YOUR_SECURE_PASSWORD_HERE"

az postgres flexible-server create \
  --resource-group $RESOURCE_GROUP \
  --name $DB_SERVER \
  --location $REGION \
  --admin-user $ADMIN_USER \
  --admin-password $ADMIN_PASSWORD \
  --sku-name Standard_B2s \
  --tier Burstable \
  --storage-size 32 \
  --version 14 \
  --public-access 0.0.0.0 \
  --high-availability Disabled \
  --backup-retention 7
```

A few notes on those flags:

- `Standard_B2s` gives you 2 vCores and 4 GB RAM, which is a reasonable starting point for production
- `storage-size 32` is 32 GB and can be expanded later without downtime
- `version 14` is what we tested against locally
- `public-access 0.0.0.0` opens access initially so you can run migrations from your machine — you will lock this down with firewall rules in the next step
- `backup-retention 7` keeps 7 days of automated backups

### 3. Create the database

```bash
DB_NAME="gov_grants"

az postgres flexible-server db create \
  --resource-group $RESOURCE_GROUP \
  --server-name $DB_SERVER \
  --database-name $DB_NAME
```

### 4. Configure firewall rules

Allow Azure services (required for App Service to reach the database):

```bash
az postgres flexible-server firewall-rule create \
  --resource-group $RESOURCE_GROUP \
  --name $DB_SERVER \
  --rule-name AllowAzureServices \
  --start-ip-address 0.0.0.0 \
  --end-ip-address 0.0.0.0
```

Allow your own IP for running migrations:

```bash
MY_IP=$(curl -s ifconfig.me)

az postgres flexible-server firewall-rule create \
  --resource-group $RESOURCE_GROUP \
  --name $DB_SERVER \
  --rule-name AllowMyIP \
  --start-ip-address $MY_IP \
  --end-ip-address $MY_IP
```

Remove the AllowMyIP rule after migrations are done if you want to tighten things up.

### 5. Get the connection string

```bash
DATABASE_URL="postgresql://${ADMIN_USER}:${ADMIN_PASSWORD}@${DB_SERVER}.postgres.database.azure.com:5432/${DB_NAME}?sslmode=require"
echo $DATABASE_URL
```

It will look something like:

```
postgresql://dbadmin:Password123@gov-grants-db.postgres.database.azure.com:5432/gov_grants?sslmode=require
```

Save this — you will need it in the next section and when setting App Service environment variables.

---

## Database Migration

### 1. Install dependencies locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Set up a local .env pointing at Azure

```bash
cat > .env << EOF
DATABASE_URL=postgresql://dbadmin:Password123@gov-grants-db.postgres.database.azure.com:5432/gov_grants?sslmode=require
AZURE_OPENAI_API_KEY=your-key-here
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
JWT_SECRET_KEY=$(openssl rand -hex 32)
EOF
```

### 3. Run migrations

```bash
alembic upgrade head
```

Expected output:

```
INFO  [alembic.runtime.migration] Running upgrade -> 125a4337b000, initial schema
INFO  [alembic.runtime.migration] Running upgrade 125a4337b000 -> head
```

### 4. Seed initial data

```bash
python -c "from app.database import seed_states; seed_states()"
```

This seeds the four states: PA, NY, MD, DC.

### 5. Verify the database

```bash
psql "$DATABASE_URL"

\dt        -- should show ~17 tables
SELECT * FROM states;
\q
```

---

## Azure App Service Setup

### 1. Create an App Service plan

```bash
APP_PLAN="gov-grants-plan"

az appservice plan create \
  --name $APP_PLAN \
  --resource-group $RESOURCE_GROUP \
  --location $REGION \
  --sku B1 \
  --is-linux
```

SKU options if you need to scale later:

- `B1` — 1 core, 1.75 GB RAM. Fine for development and low traffic
- `P1V2` — 1 core, 3.5 GB RAM. Recommended for production
- `P2V2` — 2 cores, 7 GB RAM. For higher traffic

### 2. Create the web app

```bash
APP_NAME="gov-grants-api"  # must be globally unique across all of Azure

az webapp create \
  --resource-group $RESOURCE_GROUP \
  --plan $APP_PLAN \
  --name $APP_NAME \
  --runtime "PYTHON:3.9"
```

Your API will be at `https://${APP_NAME}.azurewebsites.net`.

### 3. Configure deployment

**Option A: Local Git push**

```bash
az webapp deployment user set \
  --user-name deployuser \
  --password "DeployPassword123!"

GIT_URL=$(az webapp deployment source config-local-git \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --query url -o tsv)

git remote add azure $GIT_URL
git push azure main
```

**Option B: GitHub Actions** — see the CI/CD section below. This is what we recommend for the long term.

### 4. Set the startup command

```bash
az webapp config set \
  --resource-group $RESOURCE_GROUP \
  --name $APP_NAME \
  --startup-file "cd app && gunicorn main:app --workers 4 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000"
```

You also need to add gunicorn to requirements.txt if it is not already there:

```bash
echo "gunicorn==20.1.0" >> requirements.txt
```

---

## Environment Variables

Set all of these before the app goes live. If any of these are missing the app will either crash on startup or behave incorrectly.

```bash
# Database
az webapp config appsettings set \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --settings DATABASE_URL="postgresql://dbadmin:Password123@gov-grants-db.postgres.database.azure.com:5432/gov_grants?sslmode=require"

# Azure OpenAI (for the enrichment pipeline — not needed for the API itself)
az webapp config appsettings set \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --settings \
    AZURE_OPENAI_API_KEY="your-key" \
    AZURE_OPENAI_ENDPOINT="https://your-resource.openai.azure.com/" \
    AZURE_OPENAI_DEPLOYMENT="gpt-4o"

# JWT
JWT_SECRET=$(openssl rand -hex 32)
az webapp config appsettings set \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --settings \
    JWT_SECRET_KEY="$JWT_SECRET" \
    ALGORITHM="HS256" \
    ACCESS_TOKEN_EXPIRE_MINUTES="60"

# CORS — set to wherever the frontend is hosted
az webapp config appsettings set \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --settings CORS_ORIGINS="https://your-frontend.com,https://www.your-frontend.com"
```

Verify everything is set:

```bash
az webapp config appsettings list \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --output table
```

---

## SSL Configuration

### 1. Force HTTPS

Do this regardless of whether you use a custom domain:

```bash
az webapp update \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --https-only true
```

### 2. Custom domain (optional)

```bash
CUSTOM_DOMAIN="api.yourdomain.com"

az webapp config hostname add \
  --webapp-name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --hostname $CUSTOM_DOMAIN
```

Add a CNAME record at your DNS provider:

```
Type:  CNAME
Name:  api
Value: gov-grants-api.azurewebsites.net
```

### 3. Free managed SSL certificate

```bash
az webapp config ssl bind \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --certificate-thumbprint auto \
  --ssl-type SNI
```

---

## CI/CD with GitHub Actions

### 1. Create a service principal for deployments

```bash
SUBSCRIPTION_ID=$(az account show --query id -o tsv)

az ad sp create-for-rbac \
  --name "gov-grants-github-deploy" \
  --role contributor \
  --scopes /subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP \
  --sdk-auth
```

Copy the entire JSON output — you need it for the next step.

### 2. Add secrets to GitHub

In your repo, go to Settings → Secrets and variables → Actions and add:

- `AZURE_CREDENTIALS` — the full JSON from step 1
- `AZURE_WEBAPP_NAME` — your app name (e.g. `gov-grants-api`)

### 3. Create the workflow

Create `.github/workflows/deploy.yml`:

```yaml
name: Deploy to Azure

on:
  push:
    branches: [main]
  workflow_dispatch:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.9'

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run tests
        run: pytest tests/ -v

  deploy:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Login to Azure
        uses: azure/login@v1
        with:
          creds: ${{ secrets.AZURE_CREDENTIALS }}

      - name: Deploy to Azure Web App
        uses: azure/webapps-deploy@v2
        with:
          app-name: ${{ secrets.AZURE_WEBAPP_NAME }}
          package: .
```

The `needs: test` line means a deployment will never happen if any of the 162 tests fail. Push to main and watch the Actions tab to confirm it works.

---

## Monitoring and Logging

### Enable Application Insights

```bash
az monitor app-insights component create \
  --app gov-grants-insights \
  --location $REGION \
  --resource-group $RESOURCE_GROUP \
  --application-type web

INSIGHTS_KEY=$(az monitor app-insights component show \
  --app gov-grants-insights \
  --resource-group $RESOURCE_GROUP \
  --query instrumentationKey -o tsv)

az webapp config appsettings set \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --settings APPINSIGHTS_INSTRUMENTATIONKEY="$INSIGHTS_KEY"
```

### View logs

Live stream (useful during initial deployment):

```bash
az webapp log tail \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP
```

Download logs:

```bash
az webapp log download \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --log-file logs.zip
```

### Set up an error rate alert

```bash
az monitor metrics alert create \
  --name high-error-rate \
  --resource-group $RESOURCE_GROUP \
  --scopes "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.Web/sites/$APP_NAME" \
  --condition "count requests/failed > 10" \
  --window-size 5m \
  --evaluation-frequency 1m \
  --action email your-email@domain.com
```

---

## Production Checklist

### Database

- [ ] PostgreSQL server created with a strong password
- [ ] Firewall rules are locked down (AllowMyIP removed after migrations)
- [ ] Automated backups enabled (7+ days)
- [ ] Migrations applied — `alembic upgrade head` ran successfully
- [ ] States seeded (PA, NY, MD, DC)

### App Service

- [ ] Production SKU selected (P1V2 or higher for live traffic)
- [ ] HTTPS-only enforced
- [ ] Custom domain configured (if applicable)
- [ ] SSL certificate bound
- [ ] Startup command set (`gunicorn` with `UvicornWorker`)
- [ ] `gunicorn` in requirements.txt

### Environment variables

- [ ] `DATABASE_URL` set
- [ ] `AZURE_OPENAI_API_KEY` set
- [ ] `JWT_SECRET_KEY` set to a randomly generated 32+ character string
- [ ] `CORS_ORIGINS` set to actual production domain(s)
- [ ] No plaintext secrets in the codebase or git history

### Security

- [ ] Rate limiting active (100/min default)
- [ ] Input validation confirmed working (try sending `state_code=X` and verify 422)
- [ ] SQL injection protected via SQLAlchemy ORM (no raw queries)
- [ ] Auth enforcement plan documented for when demo period ends

### Monitoring

- [ ] Application Insights enabled
- [ ] Log streaming tested
- [ ] Error rate alert configured
- [ ] Uptime check set up (Azure Monitor or external tool)

### Testing

- [ ] All 162 tests passing locally
- [ ] Health check returns 200 on the deployed URL
- [ ] `/opportunities` returns grant data
- [ ] Swagger docs load at `/docs`
- [ ] Rate limiting tested (101 requests in a minute returns 429)

### CI/CD

- [ ] GitHub Actions workflow deployed successfully at least once
- [ ] A failed test prevents deployment (verify by breaking a test temporarily)
- [ ] Rollback plan: `git revert` + push, or deploy previous release tag

---

## Troubleshooting

### Database connection refused

```
sqlalchemy.exc.OperationalError: could not connect to server
```

Check in this order:

1. Firewall rules — does the App Service IP have access?
2. `DATABASE_URL` format — username, password, hostname, and `?sslmode=require` must all be correct
3. Test the connection string from your local machine: `psql "$DATABASE_URL"`
4. Make sure `psycopg2-binary` is in requirements.txt (it is, but worth confirming after any dep changes)

### App won't start / container health check fails

```
Container didn't respond to HTTP pings on port 8000
```

1. Check the startup command is correct — the most common issue is wrong path or missing `gunicorn`
2. Check logs: `az webapp log tail --name $APP_NAME --resource-group $RESOURCE_GROUP`
3. Make sure `gunicorn` is in requirements.txt
4. Verify `app/main.py` has `app = FastAPI(...)` at module level

### 500 errors after deployment

1. Open Application Insights and look at the Failures blade for the full stack trace
2. Check logs for the specific exception
3. The most common causes are a missing environment variable or a migration that did not run
4. Test locally with `DATABASE_URL` pointing at the Azure database to rule out code issues

### Slow responses or timeouts

1. Upgrade the App Service SKU — B1 is genuinely too small for sustained traffic
2. Check if any queries are doing full table scans (add an index on `status`, `state_id`, `deadline`)
3. Look at the database CPU and connection count in Azure Monitor
4. Consider adding connection pooling if you see many short-lived connections

### Tests pass locally but fail in GitHub Actions

1. Check that the Python version in the workflow (`3.9`) matches what you develop with locally
2. Make sure pytest and all test dependencies are in requirements.txt (not just locally installed)
3. The tests use an in-memory SQLite database — if something in your code hardcodes a Postgres-specific type it will fail in CI
4. Look at the full Actions log, not just the summary — the specific assertion failure is usually in the last 50 lines

---

## Post-Deployment Verification

After every deployment, run through these:

```bash
# Health check
curl https://gov-grants-api.azurewebsites.net/health
# Expect: {"status": "ok"}

# Basic API call
curl "https://gov-grants-api.azurewebsites.net/opportunities?per_page=3"
# Expect: JSON with total, page, per_page, data

# Auth
curl -X POST https://gov-grants-api.azurewebsites.net/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=demo@example.com&password=test"
# Expect: {"access_token": "eyJ...", "token_type": "bearer"}
```

And open `https://gov-grants-api.azurewebsites.net/docs` in a browser to confirm Swagger loads.

---

## Maintenance

### Manual database backup

```bash
az postgres flexible-server backup create \
  --resource-group $RESOURCE_GROUP \
  --name $DB_SERVER \
  --backup-name manual-backup-$(date +%Y%m%d)
```

### List backups

```bash
az postgres flexible-server backup list \
  --resource-group $RESOURCE_GROUP \
  --name $DB_SERVER
```

### Scale App Service

```bash
az appservice plan update \
  --name $APP_PLAN \
  --resource-group $RESOURCE_GROUP \
  --sku P2V2
```

### Scale the database

```bash
az postgres flexible-server update \
  --resource-group $RESOURCE_GROUP \
  --name $DB_SERVER \
  --sku-name Standard_D2s_v3 \
  --storage-size 64
```

---

Last Updated: April 2026
Platform: Microsoft Azure
Status: Production-ready
