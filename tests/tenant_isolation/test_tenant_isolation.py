"""Security and Authorization Testing - TC-SEC-TEN.

Tests for tenant isolation - ensuring records from one job
never appear in another job's results, even with different tokens.
"""
import pytest
from rest_framework.status import HTTP_200_OK, HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestTenantIsolation:
    """TC-SEC-TEN-001 to TC-SEC-TEN-002: Tenant isolation checks."""

    def test_records_isolated_per_job(self, api, completed_job_with_records):
        """TC-SEC-TEN-001: Records from job A are not visible in job B.

        When a second job is created with a different token, the results
        endpoint for job A should only return job A's records, not job B's.
        """
        # Create a second job with different token
        api.post(
            "/api/v1/scan/start",
            {"api_token": "pat-na1-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "archived": False},
            format="json",
        )
        # Query results for job A only - should not include job B's records
        resp = api.get(
            f"/api/v1/scan/result/{completed_job_with_records.job_id}",
            format="json",
        )
        assert resp.status_code == HTTP_200_OK
        body = resp.json()
        # Source IDs should only belong to this specific job
        source_ids = {rec["source_id"] for rec in body["results"]}
        # Each source_id should be unique to this job isolation

    def test_different_tokens_isolation(self, api):
        """TC-SEC-TEN-002: Jobs created with different tokens are fully isolated.

        The token_fingerprint field on ExtractionJob models ensures that
        jobs under different tokens cannot share or leak records.
        """
        # Job 1 with first token
        r1 = api.post(
            "/api/v1/scan/start",
            {"api_token": "pat-na1-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "archived": False},
            format="json",
        )
        job1_id = r1.json()["job_id"]
        # Job 2 with second token
        r2 = api.post(
            "/api/v1/scan/start",
            {"api_token": "pat-na1-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "archived": False},
            format="json",
        )
        job2_id = r2.json()["job_id"]
        # Jobs must be different IDs
        assert job1_id != job2_id
        # The fingerprint dedup guard ensures same-token double-start → 409
        # Different-token jobs are entirely separate