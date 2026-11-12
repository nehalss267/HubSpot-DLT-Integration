# HubSpot-DLT-Integration

Django REST API for extracting HubSpot deals. The service runs extraction jobs
in the background and exposes endpoints to start, monitor, cancel, and remove
jobs. Swagger UI and ReDoc are available for interactive documentation.

## Requirements

- Python 3.11+
- pip
- A HubSpot private app token with `crm.objects.deals.read`

## Setup

Clone the repository:

```bash
git clone https://github.com/nehalss267/HubSpot-DLT-Integration.git
cd HubSpot-DLT-Integration
```

Create and activate a virtual environment.

Windows:

```bash
python -m venv venv
venv\Scripts\activate
```

macOS and Linux:

```bash
python -m venv venv
source venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Create `.env` from the example file:

```bash
cp .env.example .env
```

Edit `.env`:

```env
DEBUG=True
SECRET_KEY=your-secret-key-here
ALLOWED_HOSTS=localhost,127.0.0.1

HUBSPOT_API_BASE_URL=https://api.hubapi.com
HUBSPOT_API_TIMEOUT=30
HUBSPOT_PAGE_SIZE=100
HUBSPOT_MAX_RETRIES=3
HUBSPOT_VALIDATE_TOKEN_ON_START=True
```

Run migrations and start the server:

```bash
python manage.py migrate
python manage.py runserver
```

The API is available at `http://localhost:8000`.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DEBUG` | `True` | Django debug mode |
| `SECRET_KEY` | development key | Django secret key |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated allowed hosts |
| `HUBSPOT_API_BASE_URL` | `https://api.hubapi.com` | HubSpot API base URL |
| `HUBSPOT_API_TIMEOUT` | `30` | Request timeout in seconds |
| `HUBSPOT_PAGE_SIZE` | `100` | Deal page size |
| `HUBSPOT_MAX_RETRIES` | `3` | Retry count for rate limits and network errors |
| `HUBSPOT_VALIDATE_TOKEN_ON_START` | `True` | Validate the token before creating a job |

## API documentation

| UI | URL |
|----|-----|
| Swagger UI | `http://localhost:8000/docs/` |
| Swagger UI alias | `http://localhost:8000/swagger/` |
| ReDoc | `http://localhost:8000/redoc/` |
| OpenAPI JSON | `http://localhost:8000/swagger.json` |
| OpenAPI YAML | `http://localhost:8000/swagger.yaml` |
| API root | `http://localhost:8000/api/` |

See [`docs/api-documentation.md`](docs/api-documentation.md) for request and
response details.

## Endpoints

### Extraction API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/health` | GET | Health check |
| `/api/v1/scan/start` | POST | Start an extraction job |
| `/api/v1/scan/status/{job_id}` | GET | Get job status |
| `/api/v1/scan/result/{job_id}` | GET | Get results for a completed job |
| `/api/v1/scan/cancel/{job_id}` | POST | Cancel a pending or running job |
| `/api/v1/scan/remove/{job_id}` | DELETE | Delete a job and its records |
| `/api/v1/jobs/jobs` | GET | List jobs |
| `/api/v1/jobs/statistics` | GET | Get job statistics |

Send the HubSpot token in the JSON body or the `X-API-Token` header. Tokens are
stored as SHA-256 fingerprints, not plain text.

```bash
curl -X POST http://localhost:8000/api/v1/scan/start \
  -H "Content-Type: application/json" \
  -d '{"api_token": "pat-na1-your-private-app-token", "limit": 100}'
```

```bash
curl http://localhost:8000/api/v1/scan/status/<job_id>
curl "http://localhost:8000/api/v1/scan/result/<job_id>?limit=100&offset=0"
curl -X POST http://localhost:8000/api/v1/scan/cancel/<job_id>
curl -X DELETE http://localhost:8000/api/v1/scan/remove/<job_id>
curl "http://localhost:8000/api/v1/jobs/jobs?status=completed"
curl http://localhost:8000/api/v1/jobs/statistics
```

### Example endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health/` | GET | Health check |
| `/api/health/ping/` | GET | Ping |
| `/api/users/` | GET | List users |
| `/api/users/{id}/` | GET | Get one user |

## Admin

The Django admin is available at `http://localhost:8000/admin/`. Create an
account with:

```bash
python manage.py createsuperuser
```

## Tests

Run the full test suite:

```bash
pytest
```

Or with coverage:

```bash
pytest --cov=api --cov-report=term-missing
```

### Quality gates

The `pytest.ini` configures the following quality gates:

- **Coverage**: `--cov=api` with `tests/htmlcov/` output
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

### Security testing

The test suite includes security tests that verify authentication,
authorization, and tenant isolation. By default the API uses `AllowAny`
for development convenience. To enable production-grade security, update
`core/settings.py` 

```python
REST_FRAMEWORK = {
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.TokenAuthentication',
    ],
}
```

Then run the security tests:

```bash
pytest tests/security/ tests/authorization/ tests/tenant_isolation/ -q --no-cov
```


## Project layout

```text
api/                  API application
  migrations/         Database migrations
  services/           HubSpot client and extraction runner
  admin.py            Admin registrations
  models.py           ExtractionJob and ExtractedRecord
  serializers.py      Request and response serializers
  urls.py             API routes
  views.py            API views
core/                 Django project settings and root URLs
docs/                 API documentation
manage.py             Django entry point
requirements.txt      Python dependencies 
.env.example          Environment variable template
```

## Deployment

1. Set `DEBUG=False`.
2. Set a new `SECRET_KEY`.
3. Set `ALLOWED_HOSTS` to the deployed domain.
4. Use PostgreSQL instead of SQLite.
5. Run `python manage.py collectstatic` and serve static files.
6. Run under Gunicorn or another WSGI server.
7. Use a task queue such as Celery or RQ for extraction jobs (the current
   implementation runs jobs in background threads; swap `start_extraction_async`
   for a Celery task for production).
