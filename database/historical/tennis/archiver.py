"""Archivado de `TennisScannedMatch`/`TennisMatchProbabilityTick` al disco
externo (`archive_db.py`) cuando la DB histórica activa crece demasiado
para el disco principal de la mini-pc -- pedido explícito del usuario
2026-07-31:

  1. Monitorear el tamaño de la DB; cuando exceda el 40% del espacio
     TOTAL del disco donde vive, disparar el archivado.
  2. Archivar la MITAD de los datos (los más viejos, por `detected_at`) a
     una SQLite local en el disco externo, con la misma estructura.
  3. Una vez archivadas, esas filas se BORRAN de la DB activa -- el
     archivado es un MOVE, nunca deja duplicados (evita que la misma fila
     cuente dos veces en las estadísticas ponderadas de
     `outer_apis/sports/tennis/volatility_calibration.py`).

Fail-closed: cualquier fallo a mitad de camino (no se pudo escribir en
el disco externo, no está montado, etc.) NO borra nada de la DB activa
-- el orden es siempre "escribir en el archivo primero, confirmar, recién
después borrar de la activa", nunca al revés."""

from __future__ import annotations

import os
import shutil
from typing import Any

from database.db import DB_BACKEND, DB_HISTORICAL_PATH, db_historical, init_db_historical
from database.historical.tennis.models import Match as TennisScannedMatch
from database.historical.tennis.models import ProbabilityTick as TennisMatchProbabilityTick
from database.historical.tennis.archive_db import (
    ArchivedTennisScannedMatch, ArchivedTennisMatchProbabilityTick,
    ArchiveNotMountedError, _ensure_archive_db_initialized,
)

ARCHIVE_TRIGGER_RATIO = 0.40  # pedido explícito: 40% del espacio TOTAL del disco
ARCHIVE_FRACTION = 0.5  # pedido explícito: la mitad de los datos


def should_archive() -> bool:
    """True si el archivo físico de `db_historical` ya ocupa >=40% del
    espacio TOTAL del disco donde vive. Fail-closed: False si el archivo
    no existe o no se puede leer el uso de disco (nunca dispara un
    archivado por error de lectura)."""
    try:
        if DB_BACKEND in {"postgres", "postgresql"}:
            mount_path = os.getenv("POSTGRES_DATA_MOUNT", "/postgres_data")
            if not os.path.exists(mount_path):
                return False
            db_historical.connect(reuse_if_open=True)
            db_size = db_historical.execute_sql(
                "SELECT pg_database_size(current_database())"
            ).fetchone()[0]
            total, _used, _free = shutil.disk_usage(mount_path)
            return total > 0 and (db_size / total) >= ARCHIVE_TRIGGER_RATIO
        if not os.path.exists(DB_HISTORICAL_PATH):
            return False
        db_size = os.path.getsize(DB_HISTORICAL_PATH)
        total, _used, _free = shutil.disk_usage(os.path.dirname(DB_HISTORICAL_PATH) or ".")
        if total <= 0:
            return False
        return (db_size / total) >= ARCHIVE_TRIGGER_RATIO
    except Exception:
        return False


def archive_oldest_half() -> dict[str, Any]:
    """Archiva la mitad más vieja (por `detected_at`) de
    `TennisScannedMatch` (+ sus ticks) al disco externo, y las borra de
    la DB activa SOLO después de confirmar que quedaron escritas ahí.

    Returns:
        {"archived_matches": int, "archived_ticks": int, "skipped": bool,
         "reason": str | None} -- `skipped=True` (con `reason`) si el
        disco externo no está montado/accesible, fail-closed: no borra
        nada de la activa en ese caso."""
    try:
        _ensure_archive_db_initialized()
    except ArchiveNotMountedError as exc:
        return {"archived_matches": 0, "archived_ticks": 0, "skipped": True, "reason": str(exc)}

    init_db_historical()

    total_matches = TennisScannedMatch.select().count()
    if total_matches == 0:
        return {"archived_matches": 0, "archived_ticks": 0, "skipped": True, "reason": "no hay partidos en la DB activa"}

    n_to_archive = max(1, total_matches // 2)
    oldest_matches = list(
        TennisScannedMatch.select().order_by(TennisScannedMatch.detected_at.asc()).limit(n_to_archive)
    )

    archived_matches = 0
    archived_ticks = 0
    matches_to_delete_ids: list[int] = []

    for match in oldest_matches:
        ticks = list(TennisMatchProbabilityTick.select().where(TennisMatchProbabilityTick.match == match))

        # 1) Escribir en el archivo PRIMERO. Si esto falla para un
        #    partido puntual, se lo salta (no se borra de la activa) --
        #    nunca se pierde data por un fallo parcial.
        try:
            archived_match, _created = ArchivedTennisScannedMatch.get_or_create(
                market_id=match.market_id,
                defaults={
                    "ticker_a": match.ticker_a, "ticker_b": match.ticker_b, "tour": match.tour,
                    "surface": match.surface, "player_a_name": match.player_a_name,
                    "player_b_name": match.player_b_name,
                    "player_a_rank_at_match": match.player_a_rank_at_match,
                    "player_b_rank_at_match": match.player_b_rank_at_match,
                    "best_of": match.best_of, "close_time": match.close_time,
                    "detected_at": match.detected_at, "winner": match.winner,
                    "settled_at": match.settled_at,
                },
            )
            for tick in ticks:
                ArchivedTennisMatchProbabilityTick.get_or_create(
                    match=archived_match, observed_at=tick.observed_at,
                    defaults={
                        "market_price_a": tick.market_price_a,
                        "market_ask_size_a": tick.market_ask_size_a,
                        "model_fair_a": tick.model_fair_a,
                        "market_price_b": tick.market_price_b,
                        "market_ask_size_b": tick.market_ask_size_b,
                        "model_fair_b": tick.model_fair_b,
                    },
                )
        except Exception:
            continue  # este partido no se pudo archivar completo -- no se borra, se reintenta el próximo ciclo

        archived_matches += 1
        archived_ticks += len(ticks)
        matches_to_delete_ids.append(match.id)

    # 2) Recién ahora, borrar de la DB ACTIVA lo que quedó confirmado en
    #    el archivo -- ticks primero (FK), después los partidos.
    if matches_to_delete_ids:
        TennisMatchProbabilityTick.delete().where(
            TennisMatchProbabilityTick.match.in_(matches_to_delete_ids)
        ).execute()
        TennisScannedMatch.delete().where(TennisScannedMatch.id.in_(matches_to_delete_ids)).execute()

    return {"archived_matches": archived_matches, "archived_ticks": archived_ticks, "skipped": False, "reason": None}


def run_archiver_cycle() -> dict[str, Any]:
    """Chequea el umbral y archiva si corresponde -- función de conveniencia
    para el worker (ver workers/tennis_history_archiver_worker.py)."""
    if not should_archive():
        return {"triggered": False}
    result = archive_oldest_half()
    return {"triggered": True, **result}
