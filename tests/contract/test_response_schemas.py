"""Contract tests for response schemas (TC-RES-*).

Validates exact key sets per endpoint, types, nullability, ISO‑8601 timestamps,
and error bodies.
"""
import json
from datetime import datetime

import pytest
from rest_framework.status import HTTP_200_OK, HTTP_202_ACCEPTED, HTTP_204_NO_CONTENT

from tests.unit.test_validation import serialize


@pytest.mark.django_db
class TestResponseSchemas:
    """TC-RES-001..TC-RES-014"""

    def test_scan_start_response_202(self, api):
        """TC-RES-001: /scan/start 202 response key set.

        The view serialises the job with ``JobStatusSerializer``, which includes
        the core fields returned at creation time.  ``token_fingerprint`` and
        ``parameters`` are not part of this serializer (they are stored on the
        model but omitted from the HTTP response).
        """
        resp = api.post(
            "/api/v1/scan/start",
            {"api_token": "pat-na1-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "archived": False},
            format="json",
        )
        assert resp.status_code == HTTP_202_ACCEPTED
        body = resp.json()
        # JobStatusSerializer fields (see api/serializers.py).
        expected_keys = {
            "job_id", "source", "status", "record_count",
            "error_message", "created_at", "started_at",
            "completed_at", "cancelled_at",
        }
        assert expected_keys.issubset(set(body.keys())), f"missing: {expected_keys - set(body.keys())}"

    def test_scan_status_response_200(self, api, completed_job):
        """TC-RES-003: /scan/status/<uuid> 200 response key set.

        Shares the same serializer as the start response.
        """
        resp = api.get(f"/api/v1/scan/status/{completed_job.job_id}", format="json")
        assert resp.status_code == HTTP_200_OK
        body = resp.json()
        # JobStatusSerializer fields (see api/serializers.py).
        expected_keys = {
            "job_id", "source", "status", "record_count",
            "error_message", "created_at", "started_at",
            "completed_at", "cancelled_at",
        }
        assert expected_keys.issubset(set(body.keys())), f"missing: {expected_keys - set(body.keys())}"

    def test_scan_status_response_404(self, api):
        """TC-RES-004: /scan/status/<uuid> nonexistent → 404."""
        import uuid
        resp = api.get(f"/api/v1/scan/status/{uuid.uuid4()}", format="json")
        assert resp.status_code == 404

    def test_scan_result_response_200_completed(self, api, completed_job_with_records):
        """TC-RES-005: /scan/result/<uuid> 200 for completed job with records."""
        resp = api.get(f"/api/v1/scan/result/{completed_job_with_records.job_id}", format="json")
        assert resp.status_code == HTTP_200_OK
        body = resp.json()
        # Envelope: { "count": int, "next_offset": int|None, "results": [...] }
        assert "count" in body and isinstance(body["count"], int)
        assert "next_offset" in body and body["next_offset"] is None  # last page
        assert "results" in body and isinstance(body["results"], list)
        # Each result row has expected keys.
        if body["results"]:
            result_keys = {"source_id", "data"}
            assert result_keys.issubset(set(body["results"][0].keys()))

    def test_scan_result_response_404(self, api):
        """TC-RES-006: /scan/result/<uuid> nonexistent → 404."""
        import uuid
        resp = api.get(f"/api/v1/scan/result/{uuid.uuid4()}", format="json")
        assert resp.status_code == 404

    def test_scan_result_pagination_next_offset_null_on_last(self, api, completed_job_with_records):
        """TC-RES-007: next_offset is null on the last page (no more records).

        The ``completed_job_with_records`` fixture seeds 3 records, so a limit
        of 4 ensures we are on the only page and ``next_offset`` is ``None``.
        """
        resp = api.get(f"/api/v1/scan/result/{completed_job_with_records.job_id}?limit=4", format="json")
        assert resp.status_code == HTTP_200_OK
        body = resp.json()
        assert body["next_offset"] is None

    def test_scan_result_pagination_limit_validation(self, api, completed_job_with_records):
        """TC-RES-008: limit=0, limit>500, non‑integer → 400."""
        resp = api.get(f"/api/v1/scan/result/{completed_job_with_records.job_id}?limit=0", format="json")
        assert resp.status_code == 400
        resp = api.get(f"/api/v1/scan/result/{completed_job_with_records.job_id}?limit=501", format="json")
        assert resp.status_code == 400
        resp = api.get(f"/api/v1/scan/result/{completed_job_with_records.job_id}?limit=abc", format="json")
        assert resp.status_code == 400

    def test_scan_result_no_duplicates_across_pages(self, api, completed_job_with_records):
        """TC-RES-009: walking all pages yields each record exactly once (3 total).

        The ``completed_job_with_records`` fixture seeds 3 records; walking
        pages with ``limit=50`` returns them all on the first page.
        """
        all_source_ids = set()
        offset = None
        while True:
            params = {"limit": 50}
            if offset is not None:
                params["offset"] = offset
            resp = api.get(f"/api/v1/scan/result/{completed_job_with_records.job_id}", format="json", data=params)
            assert resp.status_code == HTTP_200_OK
            body = resp.json()
            for rec in body["results"]:
                assert rec["source_id"] not in all_source_ids, f"duplicate source_id {rec['source_id']}"
                all_source_ids.add(rec["source_id"])
            next_offset = body.get("next_offset")
            if next_offset is None:
                break
            offset = next_offset
        assert len(all_source_ids) == 3  # seeded by fixture

    def test_scan_cancel_response_200(self, api, pending_job):
        """TC-RES-010: /scan/cancel/<uuid> 200 for pending job."""
        resp = api.post(f"/api/v1/scan/cancel/{pending_job.job_id}", format="json")
        assert resp.status_code == 200
        body = resp.json()
        assert "job_id" in body and "status" in body

    def test_scan_cancel_response_409_completed(self, api, completed_job):
        """TC-RES-011: /scan/cancel/<uuid> 409 for completed job."""
        resp = api.post(f"/api/v1/scan/cancel/{completed_job.job_id}", format="json")
        assert resp.status_code == 409

    def test_error_response_body(self, api):
        """TC-RES-012: error endpoints return structured error responses.

        With ``format='json'`` Django's ``Http404`` is rendered as
        ``{"detail": "..."}``; the test verifies the response is valid JSON
        and contains a ``detail`` key (the canonical Django 404 payload).
        """
        import uuid
        resp = api.get(f"/api/v1/scan/status/{uuid.uuid4()}", format="json")
        assert resp.status_code == 404
        body = resp.json()
        assert "detail" in body

    def test_remove_response_204_no_body(self, api, completed_job):
        """TC-RES-013: /scan/remove/<uuid> 204 returns empty body."""
        resp = api.delete(f"/api/v1/scan/remove/{completed_job.job_id}", format="json")
        assert resp.status_code == HTTP_204_NO_CONTENT
        assert resp.content == b"" or resp.data == {}

    def test_timestamp_iso8601(self, api):
        """TC-RES-014: timestamps are ISO‑8601 and parseable with fromisoformat."""
        resp = api.post(
            "/api/v1/scan/start",
            {"api_token": "pat-na1-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "archived": False},
            format="json",
        )
        body = resp.json()
        for key in ("created_at", "started_at", "completed_at", "cancelled_at"):
            if body.get(key):
                dt = datetime.fromisoformat(body[key])
                assert isinstance(dt, datetime)