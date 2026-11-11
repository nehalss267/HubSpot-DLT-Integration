"""unit tests: deal data transformations.

Target: ``api.services.extraction.transform_deal`` - pure function, no DB.
"""

import pytest

from api.services.extraction import transform_deal

from tests.fixtures.extraction_data import make_deal

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("deal_id", ["10241", 10241])
def test_transform_maps_provider_id_to_id_from_service(deal_id):
    """TC-TRANS-001: provider deal id becomes string ``id_from_service``.

    Precondition: deal payload with an id.
    Expected: output['id_from_service'] == str(id).
    """
    deal = {"id": deal_id, "properties": {}}
    assert transform_deal(deal)["id_from_service"] == str(deal_id)


def test_transform_merges_properties():
    """TC-TRANS-002: deal properties are merged into the record data."""
    deal = {"id": "1", "properties": {"dealname": "Acme", "amount": "9000"}}
    data = transform_deal(deal)
    assert data["dealname"] == "Acme"
    assert data["amount"] == "9000"


def test_transform_copies_source_fields():
    """TC-TRANS-003: createdAt/updatedAt/archived/archivedAt are preserved."""
    deal = make_deal("7")
    data = transform_deal(deal)
    assert data["createdAt"] == deal["createdAt"]
    assert data["updatedAt"] == deal["updatedAt"]
    assert data["archived"] is deal["archived"]


def test_transform_omits_null_source_fields():
    """TC-TRANS-004: null source fields are not copied into the record."""
    deal = {"id": "1", "properties": {}, "createdAt": None, "archivedAt": None}
    data = transform_deal(deal)
    assert "createdAt" not in data
    assert "archivedAt" not in data


def test_transform_handles_missing_id():
    """TC-TRANS-005: deals without an id map to an empty id_from_service."""
    assert transform_deal({"properties": {"dealname": "x"}})["id_from_service"] == ""


@pytest.mark.parametrize("properties", [None, {}, {"missing": "key"}])
def test_transform_handles_missing_or_empty_properties(properties):
    """TC-TRANS-006/007: missing, null and empty property maps do not crash."""
    data = transform_deal({"id": "5", "properties": properties})
    assert data["id_from_service"] == "5"


def test_transform_does_not_invent_fields():
    """TC-TRANS-008: output contains exactly id, properties and source fields."""
    deal = make_deal("9")
    data = transform_deal(deal)
    expected_keys = {"id_from_service", *deal["properties"], "createdAt", "updatedAt", "archived"}
    assert set(data) == expected_keys
