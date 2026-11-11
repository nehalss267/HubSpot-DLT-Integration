"""unit tests: request validation rules.

Targets: ``api.serializers.ScanStartSerializer``, ``api.views._pagination``,
``api.services.extraction.token_fingerprint``.
"""

import hashlib

import pytest
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from api.serializers import ScanStartSerializer
from api.services.extraction import token_fingerprint
from api.views import _pagination

from tests.fixtures.extraction_data import (
    MALFORMED_TOKEN,
    SHORT_TOKEN,
    VALID_TOKEN,
    WHITESPACE_TOKEN,
)

pytestmark = pytest.mark.unit


def serialize(payload) -> ScanStartSerializer:
    serializer = ScanStartSerializer(data=payload)
    serializer.is_valid()
    return serializer


class TestApiTokenValidation:
    def test_valid_token_is_accepted(self):
        """TC-VAL-001: tokens matching ^[A-Za-z0-9._-]{20,}$ pass validation."""
        assert serialize({"api_token": VALID_TOKEN}).is_valid() is True

    @pytest.mark.parametrize(
        "token",
        [SHORT_TOKEN, MALFORMED_TOKEN, WHITESPACE_TOKEN, "a", "", "drop table users;"],
        ids=["too-short", "bad-characters", "whitespace-only", "single-char", "empty", "sql-injection"],
    )
    def test_invalid_tokens_are_rejected(self, token):
        """TC-VAL-002/003/010: short, malformed, injection-shaped tokens -> 400 field error."""
        assert serialize({"api_token": token}).is_valid() is False

    def test_api_token_is_required(self):
        """TC-VAL-004: omitting api_token fails validation."""
        assert serialize({}).is_valid() is False

    def test_api_token_is_trimmed(self):
        """TC-VAL-005: surrounding whitespace is stripped before validation."""
        serializer = serialize({"api_token": f"  {VALID_TOKEN}  "})
        assert serializer.is_valid() is True
        assert serializer.validated_data["api_token"] == VALID_TOKEN


class TestParameterValidation:
    def test_full_valid_payload_is_accepted(self):
        """TC-VAL-006: api_token + properties + limit + archived all validate."""
        serializer = serialize(
            {
                "api_token": VALID_TOKEN,
                "properties": ["dealname", "amount"],
                "limit": 50,
                "archived": True,
            }
        )
        assert serializer.is_valid() is True
        assert serializer.validated_data["archived"] is True

    def test_properties_must_be_a_list(self):
        """TC-VAL-007: a bare string instead of a list is rejected."""
        assert serialize({"api_token": VALID_TOKEN, "properties": "dealname"}).is_valid() is False

    def test_properties_must_not_be_empty(self):
        """TC-VAL-008: an empty properties list is rejected."""
        assert serialize({"api_token": VALID_TOKEN, "properties": []}).is_valid() is False

    @pytest.mark.parametrize("limit", [0, -1, 101, 1000], ids=["zero", "negative", "above-max", "far-above-max"])
    def test_limit_out_of_bounds_is_rejected(self, limit):
        """TC-VAL-009: limit must be between 1 and 100."""
        assert serialize({"api_token": VALID_TOKEN, "limit": limit}).is_valid() is False

    @pytest.mark.parametrize("limit", [1, 50, 100], ids=["min", "middle", "max"])
    def test_limit_bounds_are_inclusive(self, limit):
        """TC-VAL-010: limit boundaries 1 and 100 are accepted."""
        assert serialize({"api_token": VALID_TOKEN, "limit": limit}).is_valid() is True

    @pytest.mark.parametrize("archived", ["banana", 5, [], "maybe"], ids=["string", "int", "list", "ambiguous-string"])
    def test_archived_must_be_a_boolean(self, archived):
        """TC-VAL-011: non-boolean archived values are rejected.

        Note: DRF deliberately coerces "true"/"1"-style literals, so only
        genuinely ambiguous values are rejected - the documented contract.
        """
        assert serialize({"api_token": VALID_TOKEN, "archived": archived}).is_valid() is False


class TestPaginationValidation:
    def make_request(self, query_string):
        # _pagination expects a DRF request exposing query_params; the raw
        # query string is embedded in the path for the factory to parse.
        return Request(APIRequestFactory().get(f"/anything?{query_string}"))

    def test_defaults(self):
        """TC-VAL-012: omitted limit/offset default to 100/0."""
        assert _pagination(self.make_request({})) == (100, 0)

    @pytest.mark.parametrize(
        "query",
        ["limit=abc", "limit=0", "limit=501", "offset=-1", "offset=abc", "offset=-10&limit=5"],
        ids=["limit-not-int", "limit-zero", "limit-over-max", "offset-negative", "offset-not-int", "both-invalid"],
    )
    def test_invalid_pagination_is_rejected(self, query):
        """TC-VAL-013: non-integer, zero/negative and over-max values raise ValidationError."""
        from rest_framework.exceptions import ValidationError

        with pytest.raises(ValidationError):
            _pagination(self.make_request(query))

    @pytest.mark.parametrize(
        "query,expected",
        [("limit=1", (1, 0)), ("limit=500", (500, 0)), ("offset=25", (100, 25))],
        ids=["limit-min", "limit-max", "offset"],
    )
    def test_valid_pagination_is_parsed(self, query, expected):
        """TC-VAL-014: string integers are parsed; boundaries accepted."""
        assert _pagination(self.make_request(query)) == expected


class TestTokenFingerprint:
    def test_fingerprint_is_sha256_hex(self):
        """TC-VAL-015: fingerprint is the SHA-256 hex digest of the token."""
        expected = hashlib.sha256(VALID_TOKEN.encode()).hexdigest()
        assert token_fingerprint(VALID_TOKEN) == expected
        assert len(token_fingerprint(VALID_TOKEN)) == 64

    def test_fingerprint_is_stable_and_distinct(self):
        """TC-VAL-016: same token -> same fingerprint; different token -> different."""
        assert token_fingerprint(VALID_TOKEN) == token_fingerprint(VALID_TOKEN)
        assert token_fingerprint(VALID_TOKEN) != token_fingerprint("pat-na1-" + "z" * 40)

    def test_fingerprint_handles_empty_and_none(self):
        """TC-VAL-017: empty/None tokens fingerprint to the empty-string digest."""
        assert token_fingerprint("") == hashlib.sha256(b"").hexdigest()
        assert token_fingerprint(None) == hashlib.sha256(b"").hexdigest()
