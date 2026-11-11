"""Security and Authorization Testing - TC-SEC-AUTHZ.

Tests for authorization checks - ensuring users can only access
resources they are permitted to access.
"""
import pytest
from rest_framework.status import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN, HTTP_200_OK, HTTP_404_NOT_FOUND, HTTP_409_CONFLICT


@pytest.mark.django_db
class TestAuthorizationEndpoints:
    """TC-SEC-AUTHZ-001 to TC-SEC-AUTHZ-007: Endpoint-level auth checks."""

    def test_status_requires_auth(self, api):
        """TC-SEC-AUTHZ-001: GET /scan/status/<job_id> requires authentication."""
        import uuid
        resp = api.get(f"/api/v1/scan/status/{uuid.uuid4()}", format="json")
        assert resp.status_code == HTTP_401_UNAUTHORIZED

    def test_results_requires_auth(self, api):
        """TC-SEC-AUTHZ-002: GET /scan/result/<job_id> requires authentication."""
        import uuid
        resp = api.get(f"/api/v1/scan/result/{uuid.uuid4()}", format="json")
        assert resp.status_code == HTTP_401_UNAUTHORIZED

    def test_cancel_requires_auth(self, api):
        """TC-SEC-AUTHZ-003: POST /scan/cancel/<job_id> requires authentication."""
        import uuid
        resp = api.post(f"/api/v1/scan/cancel/{uuid.uuid4()}", format="json")
        assert resp.status_code == HTTP_401_UNAUTHORIZED

    def test_remove_requires_auth(self, api):
        """TC-SEC-AUTHZ-004: DELETE /scan/remove/<job_id> requires authentication."""
        import uuid
        resp = api.delete(f"/api/v1/scan/remove/{uuid.uuid4()}", format="json")
        assert resp.status_code == HTTP_401_UNAUTHORIZED

    def test_list_requires_auth(self, api):
        """TC-SEC-AUTHZ-005: GET /api/v1/jobs/jobs requires authentication."""
        resp = api.get("/api/v1/jobs/jobs", format="json")
        assert resp.status_code == HTTP_401_UNAUTHORIZED

    def test_statistics_requires_auth(self, api):
        """TC-SEC-AUTHZ-006: GET /api/v1/jobs/statistics requires authentication."""
        resp = api.get("/api/v1/jobs/statistics", format="json")
        assert resp.status_code == HTTP_401_UNAUTHORIZED

    def test_health_requires_auth(self, api):
        """TC-SEC-AUTHZ-007: GET /api/v1/health requires authentication."""
        resp = api.get("/api/v1/health", format="json")
        assert resp.status_code == HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestAuthorizationJobActions:
    """TC-SEC-AUTHZ-008 to TC-SEC-AUTHZ-012: Job action auth checks."""

    def test_start_requires_auth(self, api):
        """TC-SEC-AUTHZ-008: POST /scan/start requires authentication."""
        resp = api.post("/api/v1/scan/start", {}, format="json")
        assert resp.status_code == HTTP_401_UNAUTHORIZED

    def test_invalid_token_returns_401(self, api):
        """TC-SEC-AUTHZ-009: Invalid token format returns 401."""
        resp = api.post(
            "/api/v1/scan/start",
            {"api_token": "invalid", "archived": False},
            format="json",
        )
        assert resp.status_code == HTTP_401_UNAUTHORIZED

    def test_token_not_echoed(self, api):
        """TC-SEC-AUTHZ-010: Token fingerprint never echoed in responses."""
        resp = api.post(
            "/api/v1/scan/start",
            {"api_token": "pat-na1-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "archived": False},
            format="json",
        )
        if resp.status_code == 202:
            body = resp.json()
            assert "api_token" not in str(body)

    def test_different_token_different_job(self, api):
        """TC-SEC-AUTHZ-011: Different tokens create separate jobs."""
        r1 = api.post(
            "/api/v1/scan/start",
            {"api_token": "pat-na1-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "archived": False},
            format="json",
        )
        r2 = api.post(
            "/api/v1/scan/start",
            {"api_token": "pat-na1-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "archived": False},
            format="json",
        )
        assert r1.json()["job_id"] != r2.json()["job_id"]

    def test_same_token_creates_409(self, api):
        """TC-SEC-AUTHZ-012: Same token creating second job returns 409."""
        api.post(
            "/api/v1/scan/start",
            {"api_token": "pat-na1-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "archived": False},
            format="json",
        )
        resp = api.post(
            "/api/v1/scan/start",
            {"api_token": "pat-na1-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "archived": False},
            format="json",
        )
        assert resp.status_code == HTTP_409_CONFLICT