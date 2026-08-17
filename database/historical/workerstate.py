"""Estado operativo persistente de workers históricos."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from peewee import BooleanField, CharField, DateTimeField, IntegerField, TextField

from database.base_models import HistoricalBaseModel
from database.db import db_historical


class HistoricalWorkerState(HistoricalBaseModel):
    class Meta:
        table_name = "historical_worker_state"

    worker_name = CharField(unique=True)
    cursor_id = IntegerField(default=0)
    cycle_started_at = DateTimeField(null=True)
    cycle_finished_at = DateTimeField(null=True)
    last_result_json = TextField(null=True)
    last_error = TextField(null=True)
    updated_at = DateTimeField()


class HistoricalEnrichmentAttempt(HistoricalBaseModel):
    class Meta:
        table_name = "historical_enrichment_attempt"
        indexes = ((("job_name", "record_id"), True),)

    job_name = CharField()
    record_id = IntegerField()
    attempt_count = IntegerField(default=0)
    retry_after = DateTimeField(null=True)
    last_error = TextField(null=True)
    updated_at = DateTimeField()


class TennisMlExperiment(HistoricalBaseModel):
    """Registro inmutable de un challenger ML; nunca activa estrategias."""

    class Meta:
        table_name = "tennis_ml_experiment"
        indexes = ((('dataset_sha256', 'algorithm'), False),)

    algorithm = CharField()
    dataset_sha256 = CharField()
    sample_count = IntegerField()
    training_through = CharField(null=True)
    metrics_json = TextField()
    parameters_json = TextField()
    artifact_path = TextField(null=True)
    eligible_for_review = BooleanField(default=False)
    promotion_status = CharField(default="challenger")
    created_at = DateTimeField()


def enrichment_can_attempt(job_name: str, record_id: int) -> bool:
    row = HistoricalEnrichmentAttempt.get_or_none(
        (HistoricalEnrichmentAttempt.job_name == job_name)
        & (HistoricalEnrichmentAttempt.record_id == record_id)
    )
    if row is None or row.retry_after is None:
        return True
    retry_after = row.retry_after
    if retry_after.tzinfo is None:
        retry_after = retry_after.replace(tzinfo=timezone.utc)
    return retry_after <= datetime.now(timezone.utc)


def record_enrichment_failure(job_name: str, record_id: int, reason: str) -> None:
    now = datetime.now(timezone.utc)
    row, _ = HistoricalEnrichmentAttempt.get_or_create(
        job_name=job_name, record_id=record_id,
        defaults={"updated_at": now},
    )
    row.attempt_count += 1
    delay_hours = min(24 * 7, 2 ** min(row.attempt_count - 1, 8))
    row.retry_after = now + timedelta(hours=delay_hours)
    row.last_error = reason[:2000]
    row.updated_at = now
    row.save()


def clear_enrichment_failure(job_name: str, record_id: int) -> None:
    HistoricalEnrichmentAttempt.delete().where(
        (HistoricalEnrichmentAttempt.job_name == job_name)
        & (HistoricalEnrichmentAttempt.record_id == record_id)
    ).execute()


def get_worker_cursor(worker_name: str, default: int = 0) -> int:
    row = HistoricalWorkerState.get_or_none(HistoricalWorkerState.worker_name == worker_name)
    return row.cursor_id if row is not None else default


def set_worker_cursor(worker_name: str, cursor_id: int) -> None:
    now = datetime.now(timezone.utc)
    with db_historical.atomic():
        row, _ = HistoricalWorkerState.get_or_create(
            worker_name=worker_name,
            defaults={"cursor_id": cursor_id, "updated_at": now},
        )
        if row.cursor_id != cursor_id:
            row.cursor_id = cursor_id
            row.updated_at = now
            row.save(only=[HistoricalWorkerState.cursor_id, HistoricalWorkerState.updated_at])


def mark_cycle_started(worker_name: str) -> None:
    now = datetime.now(timezone.utc)
    row, _ = HistoricalWorkerState.get_or_create(
        worker_name=worker_name, defaults={"updated_at": now},
    )
    row.cycle_started_at = now
    row.last_error = None
    row.updated_at = now
    row.save()


def mark_cycle_finished(worker_name: str, result: dict[str, Any]) -> None:
    now = datetime.now(timezone.utc)
    row, _ = HistoricalWorkerState.get_or_create(
        worker_name=worker_name, defaults={"updated_at": now},
    )
    row.cycle_finished_at = now
    row.last_result_json = json.dumps(result, default=str)
    row.last_error = None
    row.updated_at = now
    row.save()


def mark_cycle_failed(worker_name: str, error: Exception) -> None:
    now = datetime.now(timezone.utc)
    row, _ = HistoricalWorkerState.get_or_create(
        worker_name=worker_name, defaults={"updated_at": now},
    )
    row.cycle_finished_at = now
    row.last_error = f"{type(error).__name__}: {error}"
    row.updated_at = now
    row.save()
