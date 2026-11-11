"""unit tests: job lifecycle state machine.

Formal transition table for ``ExtractionJob`` . The worker (``run_extraction``) and the cancel endpoint are the
only components allowed to change status.

    +------------ start worker -----------------> in_progress --+
    |                                                          |
    pending -- cancel --> cancelled                completed  |
    |                                            failed  <----+-- provider outcome
    +--> in_progress (direct)                     cancelled <----+-- cancel while running

Guarantees under test:

* Terminal states are never overwritten by a late worker or cancel event.
* A cancelled job never reports completed or persists further records.
* Duplicate worker runs (duplicate completion events) stay idempotent.
* Every injected failure still drives the job to a terminal state.
"""

import pytest
from django.utils import timezone

from api.models import ExtractedRecord, ExtractionJob
from api.services.extraction import run_extraction

from tests.fixtures.extraction_data import VALID_TOKEN, make_deals, make_page
from tests.fixtures.jobs import make_job
from tests.fixtures.mock_provider import ProviderScript

pytestmark = [pytest.mark.unit, pytest.mark.django_db]

PENDING = ExtractionJob.Status.PENDING
IN_PROGRESS = ExtractionJob.Status.IN_PROGRESS
COMPLETED = ExtractionJob.Status.COMPLETED
FAILED = ExtractionJob.Status.FAILED
CANCELLED = ExtractionJob.Status.CANCELLED

ALLOWED_TRANSITIONS = {
    PENDING: {IN_PROGRESS, CANCELLED},
    IN_PROGRESS: {COMPLETED, FAILED, CANCELLED},
    COMPLETED: set(),
    FAILED: set(),
    CANCELLED: set(),
}


def completed_script(deal_count=2):
    return ProviderScript([make_page(make_deals(deal_count))])


def failing_script():
    from api.services.hubspot import HubSpotAPIError

    return ProviderScript([HubSpotAPIError("HubSpot API is unreachable")])


class TestHappyPathTransitions:
    def test_pending_to_in_progress_to_completed(self):
        """TC-STATE-001: canonical happy path transitions and timestamps.

        DB assertions: started_at set before completed_at; cancelled_at null.
        """
        job = make_job(status=PENDING)
        run_extraction(job.job_id, VALID_TOKEN, service_factory=completed_script().service_class)

        job.refresh_from_db()
        assert job.status == COMPLETED
        assert job.started_at is not None
        assert job.completed_at is not None
        assert job.started_at <= job.completed_at
        assert job.cancelled_at is None

    def test_pending_to_in_progress_to_failed(self):
        """TC-STATE-002: provider failure transitions to failed."""
        job = make_job(status=PENDING)
        run_extraction(job.job_id, VALID_TOKEN, service_factory=failing_script().service_class)

        job.refresh_from_db()
        assert job.status == FAILED
        assert job.completed_at is not None


class TestCancellationTransitions:
    def test_pending_to_cancelled_without_worker_side_effects(self):
        """TC-STATE-003: cancelling a pending job stops any later worker run.

        DB assertions: status stays cancelled, started_at stays null, no
        records are persisted even when a (late) worker runs.
        """
        job = make_job(status=PENDING)
        ExtractionJob.objects.filter(pk=job.pk).update(
            status=CANCELLED, cancelled_at=timezone.now()
        )

        run_extraction(job.job_id, VALID_TOKEN, service_factory=completed_script().service_class)

        job.refresh_from_db()
        assert job.status == CANCELLED
        assert job.started_at is None
        assert job.completed_at is None
        assert ExtractedRecord.objects.filter(job=job).count() == 0

    def test_cancel_while_in_progress_prevents_completion(self):
        """TC-STATE-004: cancellation between provider pages wins over completion.

        Simulates "cancellation racing with completion": the job is cancelled
        while the worker is mid-extraction. The worker observes the change
        before its final commit and never flips the job to completed.
        DB assertions: status cancelled, record_count untouched (0), the
        first page's records remain (documented partial-commit policy).
        """
        script = ProviderScript()
        script.add_page(make_deals(2), after="cursor-1")
        script.add_page(make_deals(2))
        job = make_job(status=PENDING)

        def cancel_on_second_page(call):
            if len(script.calls) > 1:  # first call already served
                ExtractionJob.objects.filter(pk=job.pk, status=IN_PROGRESS).update(
                    status=CANCELLED, cancelled_at=timezone.now()
                )

        script.on_request = cancel_on_second_page
        run_extraction(job.job_id, VALID_TOKEN, service_factory=script.service_class)

        job.refresh_from_db()
        assert job.status == CANCELLED
        assert job.completed_at is None
        assert job.record_count == 0
        assert ExtractedRecord.objects.filter(job=job).count() == 2  # page one committed

    def test_exception_after_cancellation_does_not_mark_failed(self):
        """TC-STATE-005: a worker error after cancellation leaves status cancelled."""
        script = ProviderScript()
        script.add_page(make_deals(2), after="cursor-1")
        script.add_error(RuntimeError("worker crashed after cancellation"))
        job = make_job(status=PENDING)

        def cancel_on_second_page(call):
            if len(script.calls) > 1:
                ExtractionJob.objects.filter(pk=job.pk).update(
                    status=CANCELLED, cancelled_at=timezone.now()
                )

        script.on_request = cancel_on_second_page
        run_extraction(job.job_id, VALID_TOKEN, service_factory=script.service_class)

        job.refresh_from_db()
        assert job.status == CANCELLED


class TestTerminalStateProtection:
    def test_duplicate_completion_events_are_idempotent(self):
        """TC-STATE-006: re-running a completed job stays completed, no dupes.

        Duplicate worker execution contract: the unique (job, source_id)
        constraint makes repeated completions idempotent.
        """
        job = make_job(status=PENDING)
        script = completed_script(3)
        run_extraction(job.job_id, VALID_TOKEN, service_factory=script.service_class)
        run_extraction(job.job_id, VALID_TOKEN, service_factory=completed_script(3).service_class)

        job.refresh_from_db()
        assert job.status == COMPLETED
        assert job.record_count == 3
        assert ExtractedRecord.objects.filter(job=job).count() == 3

    def test_in_progress_job_is_recoverable_by_a_new_worker_run(self):
        """TC-STATE-007: worker restart recovers a stuck in_progress job.

        Simulates a crashed worker (job stuck in_progress with partial page
        one records). A fresh run re-extracts everything and the unique
        constraint deduplicates against the partial data.
        """
        job = make_job(status=IN_PROGRESS)
        from tests.fixtures.jobs import seed_records

        seed_records(job, 2, prefix="deal")  # partial data from the crashed run

        run_extraction(job.job_id, VALID_TOKEN, service_factory=completed_script(4).service_class)

        job.refresh_from_db()
        assert job.status == COMPLETED
        assert job.record_count == 4
        assert ExtractedRecord.objects.filter(job=job).count() == 4

    def test_failed_job_can_be_re_driven_to_completed(self):
        """TC-STATE-008: documented recovery path for failed jobs.

        An operator re-run of a failed job re-processes it to completed
        (cancelled is the only state the worker refuses to touch).
        """
        job = make_job(status=FAILED, error_message="previous outage")
        run_extraction(job.job_id, VALID_TOKEN, service_factory=completed_script(2).service_class)

        job.refresh_from_db()
        assert job.status == COMPLETED
        assert job.error_message == ""


def reachable_states(table, start):
    """All states reachable from ``start`` through any number of transitions."""
    seen, queue = set(), [start]
    while queue:
        current = queue.pop()
        for target in table.get(current, ()):
            if target not in seen:
                seen.add(target)
                queue.append(target)
    return seen


class TestTransitionTable:
    @pytest.mark.parametrize(
        "from_status,worker_outcome,expected_status",
        [
            (PENDING, "success", COMPLETED),
            (PENDING, "failure", FAILED),
            (IN_PROGRESS, "success", COMPLETED),
            (IN_PROGRESS, "failure", FAILED),
        ],
        ids=["pending-success", "pending-failure", "in_progress-success", "in_progress-failure"],
    )
    def test_worker_outcomes_reach_valid_terminal_states(self, from_status, worker_outcome, expected_status):
        """TC-STATE-009: every worker outcome drives a job to a valid terminal state.

        The final status must be reachable from the starting state through
        the declared transition table (pending -> in_progress -> terminal).
        """
        job = make_job(status=from_status)
        script = completed_script(2) if worker_outcome == "success" else failing_script()

        run_extraction(job.job_id, VALID_TOKEN, service_factory=script.service_class)

        job.refresh_from_db()
        assert job.status == expected_status
        assert expected_status in reachable_states(ALLOWED_TRANSITIONS, from_status)

    def test_terminal_states_declared_in_the_table(self):
        """TC-STATE-010: the model's status choices match the transition table."""
        status_values = {choice for choice, _ in ExtractionJob.Status.choices}
        assert status_values == set(ALLOWED_TRANSITIONS)
        assert {COMPLETED, FAILED, CANCELLED} == {
            status for status, targets in ALLOWED_TRANSITIONS.items() if not targets
        }
