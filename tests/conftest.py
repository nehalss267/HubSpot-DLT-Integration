"""Shared pytest fixtures for the Data Extraction Service test suite.

Test-environment guarantees:

* **Hermetic by default.** ``hermetic`` (autouse) disables start-time token
  validation and replaces ``start_extraction_async`` with a recorder, so no
  test ever performs a real HTTP request or spawns an uncontrolled thread
  unless it explicitly opts in.
* **Isolation.** Synchronous tests run inside a rolled-back transaction
  (``django_db``); thread-based tests use ``django_db(transaction=True)``
  followed by a table flush.
* **Concurrency-safe database.** The default in-memory SQLite test database
  (``file:memorydb_...?cache=shared``) raises immediate ``database table is
  locked`` errors when background worker threads write concurrently. Tests
  therefore run against a throwaway file-backed SQLite database in WAL mode
  with a generous busy timeout, so concurrent writers serialise safely.
"""

import os
import shutil
import tempfile

import pytest
from rest_framework.test import APIClient

from api.models import ExtractionJob
from api.services.extraction import run_extraction

from .fixtures.extraction_data import OTHER_VALID_TOKEN, VALID_TOKEN
from .fixtures.jobs import make_job, make_record, seed_records

PENDING = ExtractionJob.Status.PENDING
IN_PROGRESS = ExtractionJob.Status.IN_PROGRESS
COMPLETED = ExtractionJob.Status.COMPLETED
FAILED = ExtractionJob.Status.FAILED
CANCELLED = ExtractionJob.Status.CANCELLED

TERMINAL_STATUSES = {COMPLETED, FAILED, CANCELLED}


# ---------------------------------------------------------------------------
# Test database configuration
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def django_db_modify_db_settings(django_db_modify_db_settings_parallel_suffix):
    """Run tests on a throwaway file-backed SQLite database.

    A file-backed database in WAL mode with a 20s busy timeout lets the
    background worker threads and request threads write concurrently (they
    queue instead of failing with ``database table is locked``). The temp
    directory is removed once the session ends.
    """
    from django.conf import settings

    tmp_dir = tempfile.mkdtemp(prefix="extraction_tests_")
    settings.DATABASES["default"]["TEST"]["NAME"] = os.path.join(tmp_dir, "test_db.sqlite3")
    settings.DATABASES["default"].setdefault("OPTIONS", {})
    settings.DATABASES["default"]["OPTIONS"].update(
        {"timeout": 20, "init_command": "PRAGMA journal_mode=WAL"}
    )
    yield
    shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Hermetic defaults (autouse)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def hermetic(monkeypatch, settings):
    """Guarantee no test talks to the network or loses its DB connection.

    1. Start-time token validation is switched off unless a test opts in
       through the ``token_validation_on`` fixture.
    2. ``start_extraction_async`` is replaced with a recorder, so posting to
       ``/api/v1/scan/start`` never spawns an uncontrolled thread. Tests that
       need a worker install their own replacement afterwards (monkeypatch
       teardown is LIFO, so ordering is safe).
    3. ``close_old_connections`` is neutralised for the worker module. The
       suite runs on a file-backed SQLite database (see below), and the
       worker's connection-hygiene call would close the test transaction's
       connection whenever a worker runs inline in the main thread. Tests
       that exercise the real background thread restore it (``real_worker``).

    Returns the list of recorded ``start_extraction_async`` calls.
    """
    settings.HUBSPOT_VALIDATE_TOKEN_ON_START = False
    recorded_starts = []

    def record_start(job_id, token, properties=None, page_size=None):
        recorded_starts.append(
            {"job_id": job_id, "token": token, "properties": properties, "page_size": page_size}
        )

    monkeypatch.setattr("api.views.start_extraction_async", record_start)
    monkeypatch.setattr("api.services.extraction.close_old_connections", lambda **_: None)
    return recorded_starts


@pytest.fixture
def token_validation_on(hermetic, settings):
    """Opt in to start-time provider token validation."""
    settings.HUBSPOT_VALIDATE_TOKEN_ON_START = True


# ---------------------------------------------------------------------------
# HTTP client and provider seams
# ---------------------------------------------------------------------------


@pytest.fixture
def api():
    """Unauthenticated DRF API client (documents the current contract)."""
    return APIClient()


@pytest.fixture
def install_provider(monkeypatch):
    """Install a scripted provider (or any service class) into both seams."""

    def _install(service_or_script):
        service_class = getattr(service_or_script, "service_class", service_or_script)
        monkeypatch.setattr("api.views.HubSpotDealsService", service_class)
        monkeypatch.setattr("api.services.extraction.HubSpotDealsService", service_class)
        return service_or_script

    return _install


@pytest.fixture
def synchronous_worker(monkeypatch):
    """Run the extraction worker inline when ``/scan/start`` is called.

    Makes multi-step workflows deterministic: by the time the start endpoint
    responds, the job has already reached its final state in the database.
    """

    def _install(service_factory):
        def run_inline(job_id, token, properties=None, page_size=None):
            run_extraction(
                job_id,
                token,
                properties=properties,
                page_size=page_size,
                service_factory=service_factory,
            )

        monkeypatch.setattr("api.views.start_extraction_async", run_inline)

    return _install


@pytest.fixture
def real_worker(monkeypatch):
    """Let ``/scan/start`` spawn the real background worker thread.

    Restores the real ``close_old_connections`` so the worker thread cleans
    up its own connection when it finishes. Returns the list of spawned
    threads so tests can join them before making final assertions.
    Requires ``django_db(transaction=True)``.
    """
    from django.db import close_old_connections as real_close
    from api.services.extraction import start_extraction_async as real_start

    monkeypatch.setattr("api.services.extraction.close_old_connections", real_close)
    threads = []

    def start_and_track(job_id, token, properties=None, page_size=None):
        thread = real_start(job_id, token, properties=properties, page_size=page_size)
        threads.append(thread)
        return thread

    monkeypatch.setattr("api.views.start_extraction_async", start_and_track)
    return threads


# ---------------------------------------------------------------------------
# Seeded job fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pending_job(db):
    return make_job(status=PENDING)


@pytest.fixture
def in_progress_job(db):
    return make_job(status=IN_PROGRESS)


@pytest.fixture
def completed_job(db):
    return make_job(status=COMPLETED)


@pytest.fixture
def failed_job(db):
    return make_job(status=FAILED)


@pytest.fixture
def cancelled_job(db):
    return make_job(status=CANCELLED)


@pytest.fixture
def completed_job_with_records(db):
    job = make_job(status=COMPLETED, record_count=3)
    seed_records(job, 3)
    return job


@pytest.fixture
def valid_token():
    return VALID_TOKEN


@pytest.fixture
def other_valid_token():
    return OTHER_VALID_TOKEN


@pytest.fixture
def record_factory(db):
    """Factory for single ``ExtractedRecord`` rows."""
    return make_record
