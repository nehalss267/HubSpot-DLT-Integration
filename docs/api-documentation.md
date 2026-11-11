# API Reference

Django REST API for extracting HubSpot deals. The service runs extraction jobs
in the background and exposes endpoints to start, monitor, cancel, and remove
jobs. Swagger UI and ReDoc are available for interactive documentation.

## Base URLs

| Environment | URL |
|-------------|-----|
| Local | `http://localhost:8000` |
| API prefix | `http://localhost:8000/api` |
| Versioned extraction API | `http://localhost:8000/api/v1` |

## Job statuses

| Status | Meaning |
|--------|---------|
| `pending` | Job created, not started |
| `in_progress` | Extraction running |
| `completed` | Extraction finished |
| `failed` | Extraction failed; read `error_message` |
| `cancelled` | Job cancelled by the client |

Transition table (per job state machine):

| From → Outcome | To |
|----------------|-----|
| `pending` → success | `in_progress` → then `completed` |
| `pending` → failure | `in_progress` → then `failed` |
| `in_progress` → success | `completed` |
| `in_progress` → failure | `failed` |

## Authentication

The Swagger UI and schema endpoints are public. The extraction start endpoint
requires a HubSpot private app access token.

Send the token in one of two places:

1. JSON field `api_token`
2. Header `X-API-Token`

Token requirements:

- At least 20 characters
- Letters, numbers, `.`, `_`, and `-` only
- Stored as a SHA-256 fingerprint, never as plain text

With `HUBSPOT_VALIDATE_TOKEN_ON_START=True` (the default), the token is checked
against HubSpot before the job is created. A rejected token returns `401`. A
HubSpot connection or API error during validation returns `502`.

## Endpoints

### Health check

```http
GET /api/v1/health
```

Response `200 OK`:

```json
{
  "status": "ok",
  "message": "Data extraction service is running successfully"
}
```

### Start extraction

```http
POST /api/v1/scan/start
Content-Type: application/json
X-API-Token: pat-na1-...
```

Body:

```json
{
  "api_token": "pat-na1-your-private-app-token",
  "properties": ["dealname", "amount", "dealstage", "closedate"],
  "limit": 100,
  "archived": false
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `api_token` | string | Yes* | Required unless sent as `X-API-Token` |
| `properties` | array of strings | No | Defaults to the standard deal property list |
| `limit` | integer | No | 1 to 100; defaults to `HUBSPOT_PAGE_SIZE` |
| `archived` | boolean | No | Defaults to `false` |

Response `202 Accepted`:

```json
{
  "job_id": "4f5c1c1e-3b2a-4f0e-9f4b-6a7c8d9e0f12",
  "source": "hubspot",
  "status": "pending",
  "record_count": 0,
  "error_message": "",
  "created_at": "2026-10-30T10:00:00Z",
  "started_at": null,
  "completed_at": null,
  "cancelled_at": null
}
```

| Status | Reason |
|--------|--------|
| `202` | Job accepted |
| `400` | Missing or invalid token, invalid JSON, or invalid fields |
| `401` | HubSpot rejected the token |
| `409` | A job is already running for this token |
| `502` | Token validation could not reach HubSpot |

Example:

```bash
curl -X POST http://localhost:8000/api/v1/scan/start \
  -H "Content-Type: application/json" \
  -d '{"api_token": "pat-na1-your-private-app-token", "limit": 100}'
```

### Get status

```http
GET /api/v1/scan/status/{job_id}
```

Response `200 OK`:

```json
{
  "job_id": "4f5c1c1e-3b2a-4f0e-9f4b-6a7c8d9e0f12",
  "source": "hubspot",
  "status": "completed",
  "record_count": 5,
  "error_message": "",
  "created_at": "2026-10-30T10:00:00Z",
  "started_at": "2026-10-30T10:00:01Z",
  "completed_at": "2026-10-30T10:00:09Z",
  "cancelled_at": null
}
```

| Status | Reason |
|--------|--------|
| `200` | Job found |
| `404` | Job not found or ID is not a UUID |

### Get results

```http
GET /api/v1/scan/result/{job_id}?limit=100&offset=0
```

| Parameter | Type | Default | Notes |
|-----------|------|---------|-------|
| `limit` | integer | `100` | 1 to 500 |
| `offset` | integer | `0` | Records to skip |

Response `200 OK`:

```json
{
  "job_id": "4f5c1c1e-3b2a-4f0e-9f4b-6a7c8d9e0f12",
  "count": 5,
  "limit": 100,
  "offset": 0,
  "next_offset": null,
  "results": [
    {
      "source_id": "12345678901",
      "data": {
        "id_from_service": "12345678901",
        "dealname": "Acme renewal",
        "amount": "25000",
        "dealstage": "closedwon",
        "pipeline": "default",
        "closedate": "2026-11-15T00:00:00.000Z",
        "dealtype": "existingbusiness",
        "hubspot_owner_id": "98765",
        "createdate": "2026-10-01T09:12:00.000Z",
        "hs_lastmodifieddate": "2026-10-20T14:30:00.000Z"
      },
      "extracted_at": "2026-10-30T10:00:05Z"
    }
  ]
}
```

| Status | Reason |
|--------|--------|
| `200` | Job completed |
| `400` | Invalid `limit` or `offset` |
| `404` | Job not found |
| `409` | Job not completed |

Next page:

```bash
curl "http://localhost:8000/api/v1/scan/result/<job_id>?limit=100&offset=100"
```

Stop when `next_offset` is `null`.

### Cancel job

```http
POST /api/v1/scan/cancel/{job_id}
```

Response `200 OK`:

```json
{
  "job_id": "4f5c1c1e-3b2a-4f0e-9f4b-6a7c8d9e0f12",
  "status": "cancelled",
  "message": "Extraction job cancelled successfully."
}
```

| Status | Reason |
|--------|--------|
| `200` | Job cancelled |
| `404` | Job not found |
| `409` | Job already completed, failed, or cancelled |

### Remove job

```http
DELETE /api/v1/scan/remove/{job_id}
```

Response: `204 No Content`.

| Status | Reason |
|--------|--------|
| `204` | Job and records deleted |
| `404` | Job not found |

After removal, status and result calls return `404`.

### List jobs

```http
GET /api/v1/jobs/jobs?limit=100&offset=0&status=completed
```

| Parameter | Type | Default | Notes |
|-----------|------|---------|-------|
| `limit` | integer | `100` | 1 to 500 |
| `offset` | integer | `0` | Jobs to skip |
| `status` | string | none | `pending`, `in_progress`, `completed`, `failed`, or `cancelled` |

Response `200 OK`:

```json
{
  "count": 2,
  "limit": 100,
  "offset": 0,
  "next_offset": null,
  "results": [
    {
      "job_id": "4f5c1c1e-3b2a-4f0e-9f4b-6a7c8d9e0f12",
      "source": "hubspot",
      "status": "completed",
      "record_count": 5,
      "error_message": "",
      "created_at": "2026-10-30T10:00:00Z",
      "started_at": "2026-10-30T10:00:01Z",
      "completed_at": "2026-10-30T10:00:09Z",
      "cancelled_at": null
    }
  ]
}
```

| Status | Reason |
|--------|--------|
| `200` | Jobs returned |
| `400` | Invalid status or pagination values |

### Statistics

```http
GET /api/v1/jobs/statistics
```

Response `200 OK`:

```json
{
  "total_jobs": 10,
  "pending_jobs": 1,
  "in_progress_jobs": 1,
  "completed_jobs": 6,
  "failed_jobs": 1,
  "cancelled_jobs": 1,
  "total_records": 42,
  "average_extraction_time_seconds": 7.35
}
```

`average_extraction_time_seconds` is `null` when no completed job has timing data.

### Example endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health/` | GET | Health check |
| `/api/health/ping/` | GET | Returns `{"message": "pong"}` |
| `/api/users/` | GET | List users |
| `/api/users/{id}/` | GET | Get one user |

## Response fields

### Job

| Field | Type | Description |
|-------|------|-------------|
| `job_id` | UUID string | Job identifier |
| `source` | string | Data source, currently `hubspot` |
| `status` | string | Job status |
| `record_count` | integer | Number of stored records |
| `error_message` | string | Failure details, empty on success |
| `created_at` | ISO 8601 | Creation time |
| `started_at` | ISO 8601 or null | Start time |
| `completed_at` | ISO 8601 or null | Finish time |
| `cancelled_at` | ISO 8601 or null | Cancellation time |

### Record

| Field | Type | Description |
|-------|------|-------------|
| `source_id` | string | HubSpot deal ID |
| `data` | object | Deal ID and extracted properties |
| `extracted_at` | ISO 8601 | Storage time |

Default deal properties:

```
dealname, amount, dealstage, pipeline, closedate, dealtype, description,
hubspot_owner_id, createdate, hs_lastmodifieddate
```

## Errors

Error responses use this shape:

```json
{
  "error": "Job is not completed yet.",
  "status": "in_progress"
}
```

Serializer errors are keyed by field:

```json
{
  "api_token": [
    "Invalid API token provided. Provide a valid private app access token."
  ]
}
```

## Pagination

The result and job list endpoints use `limit` and `offset`:

- `limit` is the page size.
- `offset` is the number of records or jobs skipped.
- `next_offset` is the next page offset, or `null` on the last page.
- `count` is the total number of matching items.

```text
offset = 0
repeat:
    GET /api/v1/scan/result/{job_id}?limit=100&offset={offset}
    process results
    if next_offset is null: stop
    offset = next_offset
```

## HubSpot request behavior

The HubSpot service:

- Sends `Authorization: Bearer <token>` on every request
- Retries `429` responses using the `Retry-After` header
- Retries network failures with exponential backoff
- Raises `RateLimitError` after `HUBSPOT_MAX_RETRIES` attempts
- Raises `InvalidTokenError` on `401` and `403`
- Sets the job to `failed` and stores the error if extraction cannot finish

| Setting | Default | Description |
|---------|---------|-------------|
| `HUBSPOT_API_BASE_URL` | `https://api.hubapi.com` | API base URL |
| `HUBSPOT_API_TIMEOUT` | `30` | Request timeout in seconds |
| `HUBSPOT_PAGE_SIZE` | `100` | Default page size |
| `HUBSPOT_MAX_RETRIES` | `3` | Retry count |
| `HUBSPOT_VALIDATE_TOKEN_ON_START` | `True` | Check the token before creating a job |

## Tests

### Quality gates

The `pytest.ini` configures the following quality gates:

- **Coverage**: `--cov=api` with `htmlcov/` output
- **Fail on warnings**: all `DeprecationWarning` / `PendingDeprecationWarning` are treated as errors
- **Marker set**: `unit / integration / contract / workflow / resilience / security / e2e`

### Test structure

The test suite is organised in three layers:

| Layer | Scope | Command |
|-------|-------|---------|
| Unit | Isolated business logic, serializers, extraction runner, job state machine | `pytest tests/unit -q --no-cov` |
| Integration | Endpoint-level tests against the database | `pytest tests/integration -q --no-cov` |
| Contract | Request/response schema contracts (JSON key sets, types, pagination) | `pytest tests/contract -q --no-cov` |

Full suite (all layers):

```bash
pytest tests/ -q --no-cov
```

### Live HubSpot extraction tests

The e2e tests require a real HubSpot access token:

```bash
HUBSPOT_TEST_TOKEN=pat-na1-your-token pytest tests/e2e -q --no-cov
```

These tests follow the full extraction lifecycle (start → poll status → fetch results → cancel/remove) and are gated behind the `HUBSPOT_TEST_TOKEN` environment variable.


## Production changes

1. Set `DEBUG=False`.
2. Set a new `SECRET_KEY`.
3. Set `ALLOWED_HOSTS` to the deployed domain.
4. Add authentication and throttling to the extraction endpoints.
5. Use PostgreSQL instead of SQLite.
6. Run `python manage.py collectstatic` and serve static files.
7. Run under Gunicorn or another WSGI server.
8. Move extraction to a task queue such as Celery or RQ instead of a background
   thread in the web process.