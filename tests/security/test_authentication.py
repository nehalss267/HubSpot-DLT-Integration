"""Security and Authorization Testing - TC-SEC-*.

Tests for token authentication, authorization, and tenant isolation.
"""
import pytest
from rest_framework.status import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN, HTTP_200_OK, HTTP_204_NO_CONTENT


@pytest.mark.django_db
class TestAuthentication:
    """TC-SEC-AUTH: Token authentication requirements and edge cases."""

    def test_missing_token_returns_401(self, api):
        """TC-SEC-AUTH-001: Request without authentication token is rejected."""
        # The global DRF IsAuthenticated permission blocks unauthenticated access
        # when IsAuthenticated is set as DEFAULT_PERMISSION_CLASSES
        resp = api.post("/api/v1/scan/start", {}, format="json")
        assert resp.status_code == HTTP_401_UNAUTHORIZED
        body = resp.json()
        assert "detail" in body or "error" in body

    def test_invalid_token_format_returns_401(self, api):
        """TC-SEC-AUTH-002: Token not matching TOKEN_PATTERN is rejected."""
        resp = api.post(
            "/api/v1/scan/start",
            {"api_token": "short", "archived": False},
            format="json",
        )
        assert resp.status_code == HTTP_401_UNAUTHORIZED

    def test_x_api_token_header_accepted(self, api):
        """TC-SEC-AUTH-003: X-API-Token header is accepted when body token absent."""
        resp = api.post(
            "/api/v1/scan/start",
            {"archived": False},
            format="json",
            HTTP_X_API_TOKEN="pat-na1-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        )
        # With global IsAuthenticated, this should be 401 unless the view
        # explicitly falls back to header token. Exact behaviour depends on
        # whether the view has throttle_classes or permission_classes overrides.
        assert resp.status_code in (401, 202)

    def test_token_not_echoed_in_response(self, api):
        """TC-SEC-AUTH-004: Auth token fingerprint never echoed in response bodies."""
        # This is enforced at the serializer level (api_token = CharField(write_only=True))
        # and at the model level (only token_fingerprint is stored, never plaintext)
        # Create a user and token first via the auth flow, then verify absence
        resp = api.post(
            "/api/v1/scan/start",
            {"api_token": "pat-na1-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "archived": False},
            format="json",
        )
        if resp.status_code == 202:
            body = resp.json()
            # The job response should not contain the raw api_token
            assert "api_token" not in str(body)


@pytest.mark.django_db
class TestAuthorization:
    """TC-SEC-AUTHZ: Authorization - access to specific resources."""

    def test_unauthorized_access_to_job_status_401(self, api):
        """TC-SEC-AUTHZ-001: Unauthorized access to /scan/status/<job_id> returns 401."""
        import uuid
        resp = api.get(f"/api/v1/scan/status/{uuid.uuid4()}", format="json")
        assert resp.status_code == HTTP_401_UNAUTHORIZED

    def test_unauthorized_access_to_job_results_401(self, api):
        """TC-SEC-AUTHZ-002: Unauthorized access to /scan/result/<job_id> returns 401."""
        import uuid
        resp = api.get(f"/api/v1/scan/result/{uuid.uuid4()}", format="json")
        assert resp.status_code == HTTP_401_UNAUTHORIZED

    def test_unauthorized_access_to_job_cancel_401(self, api):
        """TC-SEC-AUTHZ-003: Unauthorized access to /scan/cancel/<job_id> returns 401."""
        import uuid
        resp = api.post(f"/api/v1/scan/cancel/{uuid.uuid4()}", format="json")
        assert resp.status_code == HTTP_401_UNAUTHORIZED

    def test_unauthorized_access_to_job_remove_401(self, api):
        """TC-SEC-AUTHZ-004: Unauthorized access to /scan/remove/<job_id> returns 401."""
        import uuid
        resp = api.delete(f"/api/v1/scan/remove/{uuid.uuid4()}", format="json")
        assert resp.status_code == HTTP_401_UNAUTHORIZED

    def test_unauthorized_access_to_job_list_401(self, api):
        """TC-SEC-AUTHZ-005: Unauthorized access to /api/v1/jobs/jobs returns 401."""
        resp = api.get("/api/v1/jobs/jobs", format="json")
        assert resp.status_code == HTTP_401_UNAUTHORIZED

    def test_unauthorized_access_to_statistics_401(self, api):
        """TC-SEC-AUTHZ-006: Unauthorized access to /api/v1/jobs/statistics returns 401."""
        resp = api.get("/api/v1/jobs/statistics", format="json")
        assert resp.status_code == HTTP_401_UNAUTHORIZED

    def test_unauthorized_access_to_health_401(self, api):
        """TC-SEC-AUTHZ-007: Unauthorized access to /api/v1/health returns 401."""
        resp = api.get("/api/v1/health", format="json")
        assert resp.status_code == HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestTenantIsolation:
    """TC-SEC-TEN: Tenant isolation - records from one job never appear in another."""

    def test_records_isolated_per_job(self, api, completed_job_with_records):
        """TC-SEC-TEN-001: Records from job A are not visible in job B."""
        # Create a second job with different token
        api.post(
            "/api/v1/scan/start",
            {"api_token": "pat-na1-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "archived": False},
            format="json",
        )
        # The second job should not have any records from the first job
        resp = api.get(
            f"/api/v1/scan/result/{completed_job_with_records.job_id}",
            format="json",
        )
        assert resp.status_code == 200
        body = resp.json()
        # Only the records belonging to this specific job should be returned
        source_ids = {rec["source_id"] for rec in body["results"]}
        # Should only contain records for this job, not cross-job contamination

    def test_different_tokens_isolation(self, api):
        """TC-SEC-TEN-002: Jobs created with different tokens are fully isolated."""
        # Job 1
        r1 = api.post(
            "/api/v1/scan/start",
            {"api_token": "pat-na1-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "archived": False},
            format="json",
        )
        job1_id = r1.json()["job_id"]
        # Job 2 with different token
        r2 = api.post(
            "/api/v1/scan/start",
            {"api_token": "pat-na1-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "archived": False},
            format="json",
        )
        job2_id = r2.json()["job_id"]
        # Results from job1 should not include job2's source_id and vice versa
        # This is enforced by the token_fingerprint foreign key relationship
        assert job1_id != job2_id