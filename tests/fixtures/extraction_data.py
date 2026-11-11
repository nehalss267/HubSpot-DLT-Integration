"""Deterministic HubSpot payloads and credentials shared by every layer.

All tests run against these fixtures so the suite is reproducible and never
depends on live HubSpot data. Live integration tests opt in separately via
``HUBSPOT_TEST_TOKEN`` (see ``tests/e2e``).
"""

VALID_TOKEN = "pat-na1-" + "a" * 40
OTHER_VALID_TOKEN = "pat-eu1-" + "b" * 40
SHORT_TOKEN = "pat-too-short"
MALFORMED_TOKEN = "pat na1 with spaces and !!"
WHITESPACE_TOKEN = " " * 25

DEFAULT_PROPERTIES = [
    "dealname",
    "amount",
    "dealstage",
    "pipeline",
    "closedate",
    "dealtype",
    "description",
    "hubspot_owner_id",
    "createdate",
    "hs_lastmodifieddate",
]


def make_deal(deal_id, dealname=None, amount=None, archived=False, extra_properties=None):
    """Build a HubSpot v3 deal payload shaped like GET /crm/v3/objects/deals."""
    properties = {
        "dealname": dealname or f"Deal {deal_id}",
        "amount": amount if amount is not None else "5000",
        "dealstage": "contractsent",
        "pipeline": "default",
    }
    if extra_properties:
        properties.update(extra_properties)
    return {
        "id": str(deal_id),
        "properties": properties,
        "createdAt": "2026-01-02T03:04:05.678Z",
        "updatedAt": "2026-02-03T04:05:06.789Z",
        "archived": archived,
        "archivedAt": None,
    }


def make_page(results, after=None):
    """Build a HubSpot deals page payload with an optional cursor."""
    paging = {"next": {"after": after, "link": "https://api.hubapi.com/next"}} if after else None
    page = {"results": list(results), "paging": paging}
    return page


def make_deals(count, prefix="deal"):
    """Build `count` deals with stable ids and distinct names."""
    return [make_deal(f"{prefix}-{index}") for index in range(count)]


SMALL_DEAL_SET = make_deals(5)
