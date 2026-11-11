"""Factory helpers for ``ExtractionJob`` / ``ExtractedRecord`` test data.

Every factory leaves the database in a coherent state (timestamps match the
status) so assertions on lifecycle fields are meaningful.
"""

import time
from datetime import timedelta

from django.utils import timezone

from api.models import ExtractedRecord, ExtractionJob


def make_job(status=ExtractionJob.Status.PENDING, **overrides):
    """Create an ``ExtractionJob`` in the given state with coherent timestamps."""
    now = timezone.now()
    defaults = {}
    if status == ExtractionJob.Status.COMPLETED:
        defaults.update(
            started_at=now - timedelta(seconds=10),
            completed_at=now - timedelta(seconds=4),
        )
    elif status == ExtractionJob.Status.FAILED:
        defaults.update(
            started_at=now - timedelta(seconds=10),
            completed_at=now,
            error_message="Upstream service unavailable",
        )
    elif status == ExtractionJob.Status.CANCELLED:
        defaults.update(cancelled_at=now)
    defaults.update(overrides)
    return ExtractionJob.objects.create(status=status, **defaults)


def make_record(job, source_id, data=None):
    """Create a single ``ExtractedRecord`` for ``job``."""
    if data is None:
        data = {
            "id_from_service": str(source_id),
            "dealname": f"Deal {source_id}",
            "amount": "5000",
        }
    return ExtractedRecord.objects.create(job=job, source_id=str(source_id), data=data)


def seed_records(job, count, prefix="deal"):
    """Bulk-create ``count`` records for ``job`` with unique stable ids."""
    return ExtractedRecord.objects.bulk_create(
        [
            ExtractedRecord(
                job=job,
                source_id=f"{prefix}-{index}",
                data={"id_from_service": index, "dealname": f"Deal {index}"},
            )
            for index in range(count)
        ]
    )


def wait_for_job(job_id, statuses, timeout=15.0, interval=0.05):
    """Poll a job until it reaches one of ``statuses``; raise on timeout."""
    deadline = time.monotonic() + timeout
    job = None
    while True:
        job = ExtractionJob.objects.filter(pk=job_id).first()
        if job is not None and job.status in statuses:
            return job
        if time.monotonic() >= deadline:
            last_status = job.status if job else "missing"
            raise AssertionError(
                f"Job {job_id} did not reach {sorted(statuses)} within {timeout}s "
                f"(last status: {last_status})"
            )
        time.sleep(interval)
