"""integration tests: POST /api/v1/scan/start.

Preconditions common to all cases: no other job exists unless stated; the
async worker is stubbed by the autouse ``hermetic`` fixture unless a test
installs a synchronous/real worker; token validation is off unless a test
uses ``token_validation_on``.
"""

import pytest

from api.models import ExtractionJob
from api.services.extraction import token_fingerprint

from tests.fixtures.extraction_data import (
    MALFORMED_TOKEN,
    OTHER_VALID_TOKEN,
    SHORT_TOKEN,
    VALID_TOKEN,
)
from tests.fixtures.jobs import make_job
from tests.fixtures.mock_provider import ProviderScript

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

START_URL = "/api/v1/scan/start"
PENDING = ExtractionJob.Status.PENDING
IN_PROGRESS = ExtractionJob.Status.IN_PROGRESS
COMPLETED = ExtractionJob.Status.COMPLETED
FAILED = ExtractionJob.Status.FAILED
CANCELLED = ExtractionJob.Status.CANCELLED


def start(api, payload=None, **extra):
    return api.post(START_URL, payload or {}, content_type="application/json", **extra)


class TestHappyPath:
    def test_valid_token_creates_pending_job(self, api, hermetic):
        """TC-START-001: valid body token -> 202 with pending JobStatus body.

        Request: POST /api/v1/scan/start {"api_token": VALID_TOKEN}.
        Expected: 202; body status == "pending"; job_id is a UUID.
        DB: exactly one job, pending, fingerprint == sha256(token).
        Cleanup: transaction rollback.
        """
        response = start(api, {"api_token": VALID_TOKEN})

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == PENDING
        job = ExtractionJob.objects.get(pk=body["job_id"])
        assert job.token_fingerprint == token_fingerprint(VALID_TOKEN)
        assert job.source == "hubspot"
        assert hermetic[0]["token"] == VALID_TOKEN

    def test_token_from_header_is_accepted(self, api):
        """TC-START-002: X-API-Token header substitutes for the body token."""
        response = api.post(
            START_URL, {}, content_type="application/json", HTTP_X_API_TOKEN=VALID_TOKEN
        )

        assert response.status_code == 202
        job = ExtractionJob.objects.get(pk=response.json()["job_id"])
        assert job.token_fingerprint == token_fingerprint(VALID_TOKEN)

    def test_body_token_takes_precedence_over_header(self, api, hermetic):
        """TC-START-003: when both are supplied the body token wins."""
        response = api.post(
            START_URL,
            {"api_token": VALID_TOKEN},
            content_type="application/json",
            HTTP_X_API_TOKEN=OTHER_VALID_TOKEN,
        )

        assert response.status_code == 202
        job = ExtractionJob.objects.get(pk=response.json()["job_id"])
        assert job.token_fingerprint == token_fingerprint(VALID_TOKEN)
        assert hermetic[0]["token"] == VALID_TOKEN

    def test_request_parameters_are_persisted_and_forwarded(self, api, hermetic):
        """TC-START-004: properties/limit/archived stored on the job and passed to the worker."""
        response = start(
            api,
            {
                "api_token": VALID_TOKEN,
                "properties": ["dealname", "amount"],
                "limit": 25,
                "archived": True,
            },
        )

        job = ExtractionJob.objects.get(pk=response.json()["job_id"])
        assert job.parameters == {
            "properties": ["dealname", "amount"],
            "limit": 25,
            "archived": True,
        }
        assert hermetic[0]["properties"] == ["dealname", "amount"]
        assert hermetic[0]["page_size"] == 25

    def test_worker_receives_job_id_and_token(self, api, hermetic):
        """TC-START-005: the worker is started exactly once with the new job id."""
        response = start(api, {"api_token": VALID_TOKEN})

        assert len(hermetic) == 1
        assert str(hermetic[0]["job_id"]) == response.json()["job_id"]


class TestRequestValidation:
    def test_missing_token_returns_400(self, api):
        """TC-START-006: no token in body or header -> 400 with field error.

        DB: no job created.
        """
        response = start(api, {})

        assert response.status_code == 400
        assert "api_token" in response.json()
        assert ExtractionJob.objects.count() == 0

    @pytest.mark.parametrize(
        "token",
        [SHORT_TOKEN, MALFORMED_TOKEN],
        ids=["too-short", "invalid-characters"],
    )
    def test_malformed_tokens_return_400(self, api, token):
        """TC-START-007: tokens failing the format regex -> 400."""
        response = start(api, {"api_token": token})

        assert response.status_code == 400
        assert "api_token" in response.json()

    def test_malformed_json_returns_400(self, api):
        """TC-START-008: syntactically invalid JSON -> 400, no job."""
        response = api.post(START_URL, data='{"api_token": ', content_type="application/json")

        assert response.status_code == 400
        assert ExtractionJob.objects.count() == 0

    def test_non_object_json_body_returns_400(self, api):
        """TC-START-009: JSON arrays/scalars are rejected with an error body."""
        response = api.post(START_URL, data='["not", "an", "object"]', content_type="application/json")

        assert response.status_code == 400
        assert response.json()["error"] == "Request body must be a JSON object."

    def test_unsupported_content_type_returns_415(self, api):
        """TC-START-010: non-JSON content types are refused before validation."""
        response = api.post(START_URL, data="api_token=whatever", content_type="text/plain")

        assert response.status_code == 415
        assert ExtractionJob.objects.count() == 0

    @pytest.mark.parametrize("limit", [0, 101], ids=["below-min", "above-max"])
    def test_limit_out_of_bounds_returns_400(self, api, limit):
        """TC-START-011: limit must be within 1..100."""
        assert start(api, {"api_token": VALID_TOKEN, "limit": limit}).status_code == 400

    def test_properties_wrong_type_returns_400(self, api):
        """TC-START-012: properties must be a non-empty list of strings."""
        assert start(api, {"api_token": VALID_TOKEN, "properties": "dealname"}).status_code == 400

    def test_archived_non_boolean_returns_400(self, api):
        """TC-START-013: archived must be a boolean (ambiguous strings rejected)."""
        assert start(api, {"api_token": VALID_TOKEN, "archived": "banana"}).status_code == 400

    def test_unknown_fields_are_ignored(self, api):
        """TC-START-014: extra body fields do not fail the request."""
        assert start(api, {"api_token": VALID_TOKEN, "unexpected": "field"}).status_code == 202


class TestProviderTokenValidation:
    def test_invalid_upstream_token_returns_401(self, api, token_validation_on, install_provider):
        """TC-START-015: provider rejects the token -> 401, no job created."""
        from api.services.hubspot import InvalidTokenError

        script = install_provider(ProviderScript())
        script.add_error(InvalidTokenError("Invalid API token provided"))

        response = start(api, {"api_token": VALID_TOKEN})

        assert response.status_code == 401
        assert "Invalid API token" in response.json()["error"]
        assert ExtractionJob.objects.count() == 0

    def test_upstream_unavailable_returns_502(self, api, token_validation_on, install_provider):
        """TC-START-016: provider outage during validation -> 502."""
        from api.services.hubspot import HubSpotAPIError

        script = install_provider(ProviderScript())
        script.add_error(HubSpotAPIError("HubSpot API is unreachable"))

        response = start(api, {"api_token": VALID_TOKEN})

        assert response.status_code == 502
        assert "Unable to validate API token" in response.json()["error"]
        assert ExtractionJob.objects.count() == 0

    def test_rate_limited_validation_returns_502(self, api, token_validation_on, install_provider):
        """TC-START-017: exhausted rate limit during validation -> 502 (not 500).

        Regression guard: RateLimitError is not an InvalidTokenError and used
        to escape the view as an unhandled 500.
        """
        from api.services.hubspot import RateLimitError

        script = install_provider(ProviderScript())
        script.add_error(RateLimitError("HubSpot rate limit exceeded"))

        response = start(api, {"api_token": VALID_TOKEN})

        assert response.status_code == 502
        assert "Unable to validate API token" in response.json()["error"]
        assert ExtractionJob.objects.count() == 0

    def test_valid_upstream_token_creates_job(self, api, token_validation_on, install_provider):
        """TC-START-018: provider accepts the token -> job created after validation."""
        script = install_provider(ProviderScript())
        script.add_page([])  # validation consumes one provider page

        response = start(api, {"api_token": VALID_TOKEN})

        assert response.status_code == 202
        assert script.calls[0]["limit"] == 1  # validation probes with limit=1


def ProviderScriptSteps():
    from tests.fixtures.mock_provider import ProviderScript

    return ProviderScript()


class TestDuplicateJobGuards:
    def test_duplicate_active_job_returns_409(self, api, hermetic):
        """TC-START-019 (idempotency): same token while job active -> 409.

        Request: two identical POSTs with VALID_TOKEN.
        Expected: first 202, second 409 with the active job_id.
        DB: exactly one job; the worker started exactly once.
        """
        first = start(api, {"api_token": VALID_TOKEN})
        second = start(api, {"api_token": VALID_TOKEN})

        assert first.status_code == 202
        assert second.status_code == 409
        assert second.json()["job_id"] == first.json()["job_id"]
        assert "already in progress" in second.json()["error"]
        assert ExtractionJob.objects.count() == 1
        assert len(hermetic) == 1

    def test_in_progress_job_also_blocks_duplicates(self, api):
        """TC-START-020: the guard covers in_progress as well as pending jobs."""
        make_job(status=IN_PROGRESS, token_fingerprint=token_fingerprint(VALID_TOKEN))

        assert start(api, {"api_token": VALID_TOKEN}).status_code == 409

    @pytest.mark.parametrize(
        "status",
        [COMPLETED, FAILED, CANCELLED],
        ids=["completed", "failed", "cancelled"],
    )
    def test_terminal_jobs_release_the_token(self, api, status):
        """TC-START-021 (idempotency): terminal states allow a new job for the token.

        DB: a second job is created after the previous one finished.
        """
        make_job(status=status, token_fingerprint=token_fingerprint(VALID_TOKEN))

        response = start(api, {"api_token": VALID_TOKEN})

        assert response.status_code == 202
        assert ExtractionJob.objects.count() == 2

    def test_different_token_is_not_blocked(self, api):
        """TC-START-022: the duplicate guard is scoped to the token (tenant)."""
        make_job(status=PENDING, token_fingerprint=token_fingerprint(VALID_TOKEN))

        response = start(api, {"api_token": OTHER_VALID_TOKEN})

        assert response.status_code == 202
        assert ExtractionJob.objects.count() == 2

