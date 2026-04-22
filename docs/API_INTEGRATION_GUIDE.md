# API Integration Guide

Audience: Runwei development team integrating the Government Grants API

Last Updated: April 18, 2026

## Quick Reference

Base URL (Development): `http://localhost:8000`
Base URL (Production): TBD, will be shared after deployment
Authentication: Optional right now (JWT tokens are accepted and verified if sent, but not required)
Rate Limit: 100 requests per minute per IP
Content-Type: `application/json`
Status: Ready for integration testing

---

## Table of Contents

1. [Getting Started](#getting-started)
2. [Authentication](#authentication)
3. [Core Endpoints](#core-endpoints)
4. [Filtering and Search](#filtering-and-search)
5. [Pagination](#pagination)
6. [Response Format](#response-format)
7. [Error Handling](#error-handling)
8. [Rate Limiting](#rate-limiting)
9. [Code Examples](#code-examples)
10. [Testing Locally](#testing-locally)

---

## Getting Started

Point your HTTP client at `http://localhost:8000` while developing. The interactive docs live at `http://localhost:8000/docs` — that page lets you call every endpoint directly from the browser, which is handy for exploring the data before writing any code.

The API serves grant opportunities scraped from government portals across Pennsylvania, New York, Maryland, and DC. Right now there are 288 opportunities in the database, with roughly 97% in active status.

---

## Authentication

Auth is wired up but not enforced on read endpoints yet. That means you can call `GET /opportunities` and all the other GET routes without a token. We set it up this way intentionally for the demo period so you can start building without any auth plumbing on your end.

When you do want to send a token (or when we switch enforcement on), here is how it works:

### Getting a token

```
POST /auth/login
Content-Type: application/x-www-form-urlencoded

username=anyone@example.com&password=anything
```

During the demo period the login endpoint accepts any username and password and returns a valid token. No user record is checked. This will change when we wire up real user accounts.

Response:
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

### Sending a token

Add it as a Bearer header:

```
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

Tokens expire after 60 minutes. Request a new one when you get a 401.

---

## Core Endpoints

### Health check

```
GET /health
```

Returns `{"status": "ok"}`. Good to ping this on startup to confirm the server is reachable.

---

### List opportunities

```
GET /opportunities
```

This is the main endpoint you will use. Returns a paginated list of grant opportunities. Supports a bunch of filters described in the next section.

Example response:
```json
{
  "total": 288,
  "page": 1,
  "per_page": 20,
  "total_pages": 15,
  "data": [
    {
      "id": 1,
      "title": "Small Business Innovation Grant",
      "opportunity_type": "grant",
      "status": "active",
      "eligibility_individual": false,
      "eligibility_organization": true,
      "data_quality_score": 0.82,
      "award_min": 10000,
      "award_max": 50000,
      "deadline": "2026-06-30T00:00:00",
      "rolling": false,
      "opportunity_url": "https://...",
      "application_url": "https://...",
      "tags": ["small business", "innovation"],
      "industry": "technology",
      "agency": {
        "id": 3,
        "code": "DCED",
        "name": "PA Department of Community and Economic Development",
        "level": "state"
      },
      "state": {
        "id": 1,
        "code": "PA",
        "name": "Pennsylvania",
        "is_active": true
      },
      "categories": []
    }
  ]
}
```

The list response intentionally leaves out `description` (the full text) to keep response sizes manageable. To get the full description, fetch the individual opportunity.

---

### Get a single opportunity

```
GET /opportunities/{id}
```

Returns everything, including the full description, source details, eligible applicant types, and any attached documents.

---

### List states

```
GET /states
```

Returns the four states in the system: PA, NY, MD, DC. Use the `code` field as the value for the `state_code` filter.

---

### List agencies

```
GET /agencies
```

Returns all agencies. Use an agency `id` as the value for the `agency_id` filter.

---

### Login

```
POST /auth/login
```

Described above in the Authentication section.

---

## Filtering and Search

All filters are passed as query parameters to `GET /opportunities`. All of them are optional and can be combined freely.

| Parameter | Type | What it does |
|-----------|------|--------------|
| `q` | string | Full-text search across title, summary, and description |
| `state_code` | string | 2-letter state code, case-insensitive. PA, NY, MD, or DC |
| `opportunity_type` | string | Usually `grant`. Could also be `loan` or `contract` |
| `status` | string | `active`, `closed`, or `draft`. Almost everything is `active` |
| `award_min` | number | Returns grants where the max award is at least this amount |
| `award_max` | number | Returns grants where the min award is at most this amount |
| `deadline_after` | datetime | ISO 8601 format |
| `deadline_before` | datetime | ISO 8601 format |
| `rolling` | boolean | `true` to see grants with no fixed deadline |
| `eligibility_individual` | boolean | `true` to see grants open to individuals |
| `eligibility_organization` | boolean | `true` to see grants open to organizations/nonprofits |
| `industry` | string | Partial match against the industry field |
| `agency_id` | integer | Filter by a specific agency |
| `needs_review` | boolean | Internal flag, not needed for the Runwei integration |

A note on award filtering: `award_min` and `award_max` do range overlap matching, not exact matching. If you pass `award_min=50000`, you get back grants that could give you at least $50k (i.e. their `award_max` is >= 50000). This is intentional — it mirrors how a user would actually search.

If you pass a state code that is not exactly 2 characters, or a negative award value, you will get a 422 back. See the Error Handling section.

---

## Pagination

Default page size is 20. Maximum is 100. Use `page` and `per_page` to navigate.

```
GET /opportunities?page=2&per_page=50
```

The response always includes `total`, `page`, `per_page`, and `total_pages` so you can build pagination UI without any extra calls.

You can also sort results with `sort_by` and `sort_order`:

```
GET /opportunities?sort_by=deadline&sort_order=asc
GET /opportunities?sort_by=award_max&sort_order=desc
```

Valid values for `sort_by`: `deadline`, `award_min`, `award_max`, `created_at`, `title`. Anything else returns a 422.

---

## Response Format

All responses are JSON. Dates are ISO 8601 strings in UTC. Nullable fields are `null`, not omitted. Lists are always arrays, never `null` (so `categories` comes back as `[]` not `null` when empty).

The `data_quality_score` field is a number from 0.0 to 1.0. It reflects how complete the data is for that grant — title, deadline, award range, application URL, etc. A score above 0.70 generally means the record is well-populated. We only serve opportunities with scores above 0.50 (or 0.70 if the award is under $5,000). You do not need to filter on this yourself, but it is there if you want to show data confidence in the UI.

---

## Error Handling

| Status | Meaning |
|--------|---------|
| 200 | Everything worked |
| 201 | Record created (POST endpoints) |
| 204 | Deleted successfully (no response body) |
| 400 | Bad request, e.g. duplicate email on user creation |
| 401 | Token missing or expired (only on protected endpoints once auth is enforced) |
| 404 | Record not found |
| 422 | Validation error — bad query param type or value out of range |
| 429 | Rate limit hit |

For 422 errors the body tells you exactly which field failed and why:

```json
{
  "detail": [
    {
      "type": "string_too_short",
      "loc": ["query", "state_code"],
      "msg": "String should have at least 2 characters",
      "input": "P"
    }
  ]
}
```

For 404:
```json
{
  "detail": "Opportunity not found"
}
```

---

## Rate Limiting

The `/opportunities` list endpoint is limited to 100 requests per minute per IP address. All other endpoints are currently unlimited, but treat them reasonably.

When you stay under the limit, the response includes these headers:

```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 87
X-RateLimit-Reset: 1714080060
```

When you hit the limit you get a 429 with:

```json
{
  "error": "Rate limit exceeded: 100 per 1 minute"
}
```

If you are running tests in a tight loop and hitting 429s, add a small delay between requests or reduce `per_page` to make fewer calls.

---

## Code Examples

### JavaScript (fetch)

```javascript
// Basic list with filters
const response = await fetch(
  'http://localhost:8000/opportunities?state_code=PA&award_min=10000&per_page=20'
);
const data = await response.json();
console.log(`${data.total} total grants, showing page ${data.page} of ${data.total_pages}`);
data.data.forEach(grant => {
  console.log(`${grant.title} — up to $${grant.award_max?.toLocaleString()}`);
});

// Get a single grant
const grant = await fetch('http://localhost:8000/opportunities/42').then(r => r.json());
console.log(grant.description);
```

### JavaScript (with auth token)

```javascript
const loginRes = await fetch('http://localhost:8000/auth/login', {
  method: 'POST',
  headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  body: 'username=demo@runwei.com&password=anything'
});
const { access_token } = await loginRes.json();

const grants = await fetch('http://localhost:8000/opportunities', {
  headers: { 'Authorization': `Bearer ${access_token}` }
}).then(r => r.json());
```

### Python (requests)

```python
import requests

base = "http://localhost:8000"

# Search for NY grants open to individuals with a deadline coming up
params = {
    "state_code": "NY",
    "eligibility_individual": True,
    "deadline_after": "2026-04-18T00:00:00",
    "sort_by": "deadline",
    "sort_order": "asc",
    "per_page": 50,
}
resp = requests.get(f"{base}/opportunities", params=params)
resp.raise_for_status()
data = resp.json()

print(f"Found {data['total']} grants")
for grant in data["data"]:
    print(grant["title"], grant["deadline"])
```

### curl

```bash
# List PA grants sorted by deadline
curl "http://localhost:8000/opportunities?state_code=PA&sort_by=deadline&sort_order=asc"

# Full-text search
curl "http://localhost:8000/opportunities?q=small+business"

# Get a single grant
curl "http://localhost:8000/opportunities/1"

# Get a token
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=demo@example.com&password=test"
```

---

## Testing Locally

To run the API on your machine:

```bash
git clone <repo-url>
cd gov-opportunities-api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
cd app
uvicorn main:app --reload
```

Then hit `http://localhost:8000/docs` in your browser to explore the API interactively.

If you just want to poke at a live endpoint without setting anything up, reach out and we can share the staging URL once it is deployed.

---

If something behaves differently from what is documented here, or you hit an endpoint that is not listed, check `http://localhost:8000/docs` — the Swagger UI is always in sync with the actual code. If you find a real discrepancy, let us know.
