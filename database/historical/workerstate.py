"""Estado operativo persistente de workers históricos."""

from __future__ import annotations

import json
import time
import zlib
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


def record_enrichment_source_unavailable(
    job_name: str, record_id: int, reason: str, *, terminal_after: int = 2,
) -> bool:
    """Registra ausencia estructural del proveedor sin reintentos infinitos.

    En partidos históricos, un feed de estadísticas vacío o no tipable no
    suele completarse después. Se permite un segundo intento para absorber
    respuestas transitorias y luego se difiere diez años. Devuelve ``True``
    cuando el registro quedó terminalmente diferido.
    """
    now = datetime.now(timezone.utc)
    row, _ = HistoricalEnrichmentAttempt.get_or_create(
        job_name=job_name, record_id=record_id,
        defaults={"updated_at": now},
    )
    row.attempt_count += 1
    terminal = row.attempt_count >= terminal_after
    if terminal:
        row.retry_after = now + timedelta(days=3650)
        reason = f"terminal: {reason}"
    else:
        row.retry_after = now + timedelta(hours=1)
    row.last_error = reason[:2000]
    row.updated_at = now
    row.save()
    return terminal


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


def startup_schedule_delay(
    worker_name: str, interval_seconds: int, *, jitter_max_seconds: int = 120,
) -> int:
    """Delay de arranque que respeta el último ciclo y escalona workers.

    Un redeploy no debe disparar nuevamente un trabajo de 6/24 horas que
    acaba de terminar. El jitter es determinístico por nombre para que los
    contenedores pesados tampoco despierten simultáneamente.
    """
    row = HistoricalWorkerState.get_or_none(
        HistoricalWorkerState.worker_name == worker_name
    )
    remaining = 0
    if row is not None and row.cycle_finished_at is not None:
        finished = row.cycle_finished_at
        if finished.tzinfo is None:
            finished = finished.replace(tzinfo=timezone.utc)
        elapsed = max(0.0, (datetime.now(timezone.utc) - finished).total_seconds())
        remaining = max(0, int(interval_seconds - elapsed))
    jitter = (
        zlib.crc32(worker_name.encode()) % (jitter_max_seconds + 1)
        if jitter_max_seconds > 0 else 0
    )
    return remaining + jitter


def wait_for_startup_schedule(
    worker_name: str, interval_seconds: int, *, jitter_max_seconds: int = 120,
) -> int:
    delay = startup_schedule_delay(
        worker_name, interval_seconds, jitter_max_seconds=jitter_max_seconds,
    )
    if delay > 0:
        print(f"[{worker_name}] startup delay={delay}s", flush=True)
        time.sleep(delay)
    return delay
