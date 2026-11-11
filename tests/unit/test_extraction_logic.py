"""unit tests: extraction runner logic.

Target: ``api.services.extraction.run_extraction`` driven synchronously
through an injected scripted provider (no threads, no HTTP).
"""

import pytest

from api.models import ExtractedRecord, ExtractionJob
from api.services.extraction import run_extraction

from tests.fixtures.extraction_data import (
    DEFAULT_PROPERTIES,
    VALID_TOKEN,
    make_deal,
    make_deals,
    make_page,
)
from tests.fixtures.jobs import make_job
from tests.fixtures.mock_provider import ProviderScript

pytestmark = [pytest.mark.unit, pytest.mark.django_db]


def make_script(steps):
    return ProviderScript(steps)


class TestSuccessfulExtraction:
    def test_single_page_extraction_completes_the_job(self):
        """TC-EXT-001: one provider page -> job completed with correct fields.

        DB assertions: status=completed, record_count=3, completed_at set,
        error_message cleared.
        """
        script = make_script([make_page(make_deals(3))])
        job = make_job(status=ExtractionJob.Status.PENDING)

        run_extraction(job.job_id, VALID_TOKEN, service_factory=script.service_class)

        job.refresh_from_db()
        assert job.status == ExtractionJob.Status.COMPLETED
        assert job.record_count == 3
        assert job.completed_at is not None
        assert job.error_message == ""
        assert ExtractedRecord.objects.filter(job=job).count() == 3

    def test_multi_page_extraction_follows_cursors(self):
        """TC-EXT-002: provider cursors are followed until paging ends."""
        script = make_script(
            [
                make_page(make_deals(2, prefix="a"), after="cursor-1"),
                make_page(make_deals(2, prefix="b"), after="cursor-2"),
                make_page(make_deals(1, prefix="c")),
            ]
        )
        job = make_job(status=ExtractionJob.Status.PENDING)

        run_extraction(job.job_id, VALID_TOKEN, service_factory=script.service_class)

        job.refresh_from_db()
        assert job.status == ExtractionJob.Status.COMPLETED
        assert job.record_count == 5
        assert [call["after"] for call in script.calls] == [None, "cursor-1", "cursor-2"]

    def test_extraction_stops_when_page_has_no_cursor(self):
        """TC-EXT-003: a page without a next cursor terminates the loop."""
        script = make_script([make_page(make_deals(2))])
        job = make_job(status=ExtractionJob.Status.PENDING)

        run_extraction(job.job_id, VALID_TOKEN, service_factory=script.service_class)

        assert len(script.calls) == 1
        job.refresh_from_db()
        assert job.status == ExtractionJob.Status.COMPLETED

    def test_extraction_stops_on_empty_page_even_with_cursor(self):
        """TC-EXT-004: an empty results page terminates even if a cursor exists."""
        script = make_script([make_page([], after="cursor-1"), make_page(make_deals(2))])
        job = make_job(status=ExtractionJob.Status.PENDING)

        run_extraction(job.job_id, VALID_TOKEN, service_factory=script.service_class)

        job.refresh_from_db()
        assert job.status == ExtractionJob.Status.COMPLETED
        assert job.record_count == 0
        assert len(script.calls) == 1  # second page never fetched

    def test_duplicate_deals_within_a_page_are_persisted_once(self):
        """TC-EXT-005: duplicate provider ids in one page yield one record."""
        deals = [make_deal("dup"), make_deal("dup"), make_deal("unique")]
        script = make_script([make_page(deals)])
        job = make_job(status=ExtractionJob.Status.PENDING)

        run_extraction(job.job_id, VALID_TOKEN, service_factory=script.service_class)

        job.refresh_from_db()
        assert job.record_count == 2
        assert sorted(
            ExtractedRecord.objects.filter(job=job).values_list("source_id", flat=True)
        ) == ["dup", "unique"]

    def test_duplicate_deals_across_pages_are_persisted_once(self):
        """TC-EXT-006: duplicate provider ids across pages yield one record."""
        script = make_script(
            [make_page([make_deal("dup")], after="c1"), make_page([make_deal("dup")])]
        )
        job = make_job(status=ExtractionJob.Status.PENDING)

        run_extraction(job.job_id, VALID_TOKEN, service_factory=script.service_class)

        job.refresh_from_db()
        assert job.record_count == 1

    def test_deals_without_id_are_skipped(self):
        """TC-EXT-007: deals missing an id are not persisted."""
        deals = [{"properties": {"dealname": "no id"}}, make_deal("has-id")]
        script = make_script([make_page(deals)])
        job = make_job(status=ExtractionJob.Status.PENDING)

        run_extraction(job.job_id, VALID_TOKEN, service_factory=script.service_class)

        job.refresh_from_db()
        assert job.record_count == 1
        assert ExtractedRecord.objects.filter(job=job, source_id="has-id").exists()

    def test_properties_and_page_size_are_forwarded_to_provider(self):
        """TC-EXT-008: requested properties/limit reach the provider calls."""
        script = make_script([make_page(make_deals(1))])
        job = make_job(status=ExtractionJob.Status.PENDING)

        run_extraction(
            job.job_id,
            VALID_TOKEN,
            properties=["dealname"],
            page_size=7,
            service_factory=script.service_class,
        )

        assert script.calls[0]["properties"] == ["dealname"]
        assert script.calls[0]["limit"] == 7

    def test_default_properties_used_when_none_requested(self):
        """TC-EXT-009: provider receives DEFAULT_DEAL_PROPERTIES when unspecified."""
        script = make_script([make_page(make_deals(1))])
        job = make_job(status=ExtractionJob.Status.PENDING)

        run_extraction(job.job_id, VALID_TOKEN, service_factory=script.service_class)

        assert script.calls[0]["properties"] == DEFAULT_PROPERTIES

    def test_empty_result_set_completes_with_zero_records(self):
        """TC-EXT-010: provider with no deals -> completed, record_count 0."""
        script = make_script([make_page([])])
        job = make_job(status=ExtractionJob.Status.PENDING)

        run_extraction(job.job_id, VALID_TOKEN, service_factory=script.service_class)

        job.refresh_from_db()
        assert job.status == ExtractionJob.Status.COMPLETED
        assert job.record_count == 0
        assert job.started_at is not None


class TestFailedExtraction:
    def test_provider_failure_marks_job_failed(self):
        """TC-EXT-011: provider error -> job failed with message and timestamp."""
        from api.services.hubspot import HubSpotAPIError

        script = make_script([HubSpotAPIError("HubSpot API is unreachable")])
        job = make_job(status=ExtractionJob.Status.PENDING)

        run_extraction(job.job_id, VALID_TOKEN, service_factory=script.service_class)

        job.refresh_from_db()
        assert job.status == ExtractionJob.Status.FAILED
        assert "unreachable" in job.error_message
        assert job.completed_at is not None
        assert job.record_count == 0

    def test_missing_job_is_a_no_op(self):
        """TC-EXT-012: running a worker for a deleted job does nothing."""
        import uuid

        run_extraction(uuid.uuid4(), VALID_TOKEN, service_factory=make_script([]).service_class)
        assert ExtractionJob.objects.count() == 0


class TestAsyncStart:
    @pytest.mark.django_db(transaction=True)
    def test_start_extraction_async_spawns_a_completing_worker(self, install_provider):
        """TC-EXT-013: the real thread runner completes an extraction.

        Preconditions: transactional DB (the worker thread needs committed
        visibility); scripted provider installed for the worker seam.
        DB assertions: job reaches completed with 3 records after joining.
        Cleanup: pytest-django flushes the database after the test.
        """
        from api.services.extraction import start_extraction_async

        install_provider(make_script([make_page(make_deals(3))]))
        job = make_job(status=ExtractionJob.Status.PENDING)

        thread = start_extraction_async(job.job_id, VALID_TOKEN)
        thread.join(timeout=10)

        job.refresh_from_db()
        assert not thread.is_alive()
        assert job.status == ExtractionJob.Status.COMPLETED
        assert job.record_count == 3

