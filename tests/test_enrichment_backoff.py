from datetime import datetime, timezone

import pytest

from database.db import init_db_historical
from database.historical.workerstate import (
    HistoricalEnrichmentAttempt, clear_enrichment_failure,
    enrichment_can_attempt, record_enrichment_failure, HistoricalWorkerState,
    record_enrichment_source_unavailable, startup_schedule_delay,
)


@pytest.fixture(autouse=True)
def clean_attempts():
    init_db_historical()
    HistoricalEnrichmentAttempt.delete().execute()
    yield
    HistoricalEnrichmentAttempt.delete().execute()


def test_failure_defers_retry_and_success_clears_it():
    assert enrichment_can_attempt('stats', 10) is True
    record_enrichment_failure('stats', 10, 'not found')
    row = HistoricalEnrichmentAttempt.get()
    assert row.attempt_count == 1
    retry_after = row.retry_after
    if retry_after.tzinfo is None:
        retry_after = retry_after.replace(tzinfo=timezone.utc)
    assert retry_after > datetime.now(timezone.utc)
    assert enrichment_can_attempt('stats', 10) is False

    clear_enrichment_failure('stats', 10)
    assert enrichment_can_attempt('stats', 10) is True


def test_repeated_failures_increase_backoff():
    record_enrichment_failure('duration', 5, 'first')
    first = HistoricalEnrichmentAttempt.get().retry_after
    record_enrichment_failure('duration', 5, 'second')
    second = HistoricalEnrichmentAttempt.get().retry_after
    assert second > first


def test_source_unavailable_becomes_terminal_after_second_attempt():
    assert record_enrichment_source_unavailable("stats", 7, "empty") is False
    assert record_enrichment_source_unavailable("stats", 7, "empty") is True
    row = HistoricalEnrichmentAttempt.get()
    assert row.attempt_count == 2
    assert row.last_error.startswith("terminal:")
    assert row.retry_after.year >= datetime.now(timezone.utc).year + 9


def test_startup_schedule_respects_recent_completion_without_randomness():
    now = datetime.now(timezone.utc)
    HistoricalWorkerState.create(
        worker_name="heavy", cycle_finished_at=now, updated_at=now,
    )
    delay = startup_schedule_delay("heavy", 3600, jitter_max_seconds=0)
    assert 3598 <= delay <= 3600
    HistoricalWorkerState.delete().where(
        HistoricalWorkerState.worker_name == "heavy"
    ).execute()
