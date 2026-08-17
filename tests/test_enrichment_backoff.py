from datetime import datetime, timezone

import pytest

from database.db import init_db_historical
from database.historical.workerstate import (
    HistoricalEnrichmentAttempt, clear_enrichment_failure,
    enrichment_can_attempt, record_enrichment_failure,
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
