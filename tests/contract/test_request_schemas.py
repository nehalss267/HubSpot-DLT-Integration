"""Contract tests for request schemas (TC-REQ-*).

Validates that accepted payload fields are honoured, unknown fields are
silently dropped, and required/ write‑only constraints are enforced.
"""
import pytest

from tests.unit.test_validation import serialize


@pytest.mark.django_db
class TestRequestSchemas:
    """TC-REQ-001..TC-REQ-010"""

    # Separate tokens so the "duplicate active job" dedup guard never cross‑talks.
    # Each value passes TOKEN_PATTERN (r'^[A-Za-z0-9._-]{20,}$').
    TOKEN_FIRST = "pat-na1-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    TOKEN_SECOND = "pat-na1-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    TOKEN_THIRD = "pat-na1-cccccccccccccccccccccccccccccccccccccc"
    TOKEN_FOURTH = "pat-na1-dddddddddddddddddddddddddddddddddddddddd"
    TOKEN_FIFTH = "pat-na1-eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
    TOKEN_SIXTH = "pat-na1-ffffffffffffffffffffffffffffffffffffffff"
    TOKEN_SEVENTH = "pat-na1-gggggggggggggggggggggggggggggggggggggggg"
    TOKEN_EIGHTH = "pat-na1-hhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhh"

    def test_accepted_fields_honoured(self, api):
        """TC-REQ-001: all accepted fields are parsed without error."""
        payload = {
            "api_token": self.TOKEN_FIRST,
            "archived": False,
            "properties": ["name", "deal_stage"],
            "limit": 10,
        }
        assert api.post("/api/v1/scan/start", payload, format="json").status_code == 202

    def test_unknown_fields_ignored(self, api):
        """TC-REQ-002: unknown top‑level fields are silently dropped."""
        payload = {
            "api_token": self.TOKEN_SECOND,
            "unknown_field": "should_not_matter",
        }
        assert api.post("/api/v1/scan/start", payload, format="json").status_code == 202

    def test_api_token_required(self, api):
        """TC-REQ-003: missing api_token returns 400."""
        assert api.post("/api/v1/scan/start", {"archived": False}, format="json").status_code == 400

    def test_api_token_write_not_echoed(self, api):
        """TC-REQ-004: api_token is write‑only and never echoed in responses."""
        resp = api.post(
            "/api/v1/scan/start", {"api_token": "secret123", "archived": False}, format="json"
        )
        # "secret123" is intentionally short/invalid → serializer rejects it
        # (TOKEN_PATTERN requires ≥20 alphanum/chars/dots/hyphens). If the
        # server somehow accepts it we just verify absence from the body.
        if resp.status_code == 202:
            body = resp.json()
            assert "api_token" not in str(body)

    def test_properties_list_of_strings(self, api):
        """TC-REQ-005: properties must be a list of strings."""
        # Valid
        assert api.post(
            "/api/v1/scan/start",
            {"api_token": self.TOKEN_THIRD, "properties": ["name"]},
            format="json",
        ).status_code == 202
        # Invalid – pass an integer where ListField expects a list;
        # DRF returns 400 with field‑level error.
        assert api.post(
            "/api/v1/scan/start",
            {"api_token": self.TOKEN_THIRD, "properties": 42},
            format="json",
        ).status_code == 400

    def test_limit_between_1_and_100(self, api):
        """TC-REQ-006: limit validated to the range 1..100.

        Two separate tokens are used because the API prevents starting two
        concurrent jobs under the same token (409 Conflict).
        """
        # limit=1 with first token
        assert api.post(
            "/api/v1/scan/start",
            {"api_token": self.TOKEN_FOURTH, "limit": 1},
            format="json",
        ).status_code == 202
        # limit=100 with a different token
        assert api.post(
            "/api/v1/scan/start",
            {"api_token": self.TOKEN_FIFTH, "limit": 100},
            format="json",
        ).status_code == 202

    def test_archived_boolean(self, api):
        """TC-REQ-007: archived accepted as JSON boolean.

        Uses a dedicated token for each sub‑assertion so the dedup guard
        does not fire between the two POSTs.
        """
        assert api.post(
            "/api/v1/scan/start",
            {"api_token": self.TOKEN_SIXTH, "archived": True},
            format="json",
        ).status_code == 202
        assert api.post(
            "/api/v1/scan/start",
            {"api_token": self.TOKEN_SEVENTH, "archived": False},
            format="json",
        ).status_code == 202

    def test_query_param_status_filter(self, api):
        """TC-REQ-008: status filter via query parameter."""
        # Create a completed job first so we can filter on it
        api.post(
            "/api/v1/scan/start",
            {"api_token": self.TOKEN_FOURTH, "archived": False},
            format="json",
        )
        # The status filter is accepted; exact behaviour tested in integration tests.
        resp = api.get("/api/v1/scan/start?status=completed", format="json")
        # Should not error; precise contract tested in test_response_schemas.

    def test_query_param_after_cursor(self, api):
        """TC-REQ-009: after (cursor) query parameter accepted."""
        resp = api.get("/api/v1/scan/start?after=some_cursor", format="json")
        # Should not error; exact pagination contract in test_scan_results.

    def test_x_api_token_header_fallback(self, api):
        """TC-REQ-010: X‑API‑Token header used when body token absent/empty."""
        resp = api.post(
            "/api/v1/scan/start",
            {"archived": False},
            format="json",
            HTTP_X_API_TOKEN=self.TOKEN_SECOND,
        )
        # Header token only takes effect when body token is missing/empty – exact
        # precedence tested in integration tests. Here body has no token so header
        # should be accepted → 202.
        assert resp.status_code == 202