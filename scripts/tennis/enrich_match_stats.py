"""Enriquecimiento de estadísticas de partido sobre `MatchHistory` --
2026-08-16, pedido explícito del usuario ("sí, agregalo") tras destrabar
`get_match_statistics` (ver `kairos-match-stats-gate-removed-2026-08-16`).

POR QUÉ HACE FALTA ESTE SCRIPT: destrabar el gate no alcanzaba. El único
productor de stats era `tennis_match_history_sync_worker::
fill_missing_stats_for_player`, que corre SOLO para los jugadores de
partidos NUEVOS detectados en Kalshi y acotado a sus 50 partidos más
recientes -- los ~4.500 partidos históricos de `MatchHistory` nunca
recibían stats por ese camino. Esto es el equivalente exacto de
`enrich_match_durations.py` pero para estadísticas, y corre desde el
worker reactivo de huecos.

FILTRO DE PARTIDO TERMINADO (aprendido de un hallazgo real, no teórico):
tras el deploy del gate aparecieron filas con TODAS las columnas
tipadas en NULL, porque el `raw_stats_json` solo traía "Last 10 balls" y
"Match points saved" -- eran partidos EN CURSO, que Flashscore todavía
no cierra. Por eso acá NO alcanza con `score IS NOT NULL` (que es lo que
filtra el sync worker): se exige además que `match_date` sea ANTERIOR a
hoy, para no gastar requests en partidos que aún no tienen estadísticas
completas y para no guardar filas basura.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from database.db import init_db_historical
from database.historical.tennis.matchstats import (
    has_match_stats_for_history, match_stats_source_id,
    remember_match_stats_source, save_match_stats_for_history_status,
)
from database.historical.tennis.models import MatchHistory
from outer_apis.scrapers.tennis.flashscore_scraper import (
    find_match_id, get_match_statistics,
)
from database.historical.tennis.models import MatchStats
from scripts.tennis.rotating_batch import take_rotating_batch
from database.historical.workerstate import (
    clear_enrichment_failure, enrichment_can_attempt, record_enrichment_failure,
    record_enrichment_source_unavailable,
)

# Mismo criterio conservador que `enrich_match_durations`: el throttle
# global de Flashscore ya espacia las requests, esto es cortesía extra
# entre partidos (cada partido son 2 requests: resolver id + stats).
_PAUSE_BETWEEN_MATCHES_S = 0.5
_batch_cursor_id = 0


def enrich_match_stats(limit: int = 40) -> dict[str, Any]:
    """Completa estadísticas reales (aces, dobles faltas, % de saque,
    break points...) de partidos de `MatchHistory` que todavía no las
    tienen.

    Solo considera partidos TERMINADOS (ver docstring del módulo):
    identidad de ambos jugadores resuelta, con marcador real, y con
    `match_date` anterior a hoy.

    `limit`: partidos por corrida -- cada uno cuesta ~2 requests reales
    a Flashscore (throttle global compartido con los otros workers), así
    que el worker lo completa de a tandas horarias.

    Returns: {"processed", "resolved", "not_found", "errors"} -- nunca
    excepciona hacia el caller, cada partido fallido se cuenta aparte."""
    init_db_historical()

    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()

    global _batch_cursor_id

    histories_with_stats = (
        MatchStats.select(MatchStats.match_history)
        .where(
            MatchStats.match_history.is_null(False)
            & (MatchStats.is_usable == True)
        )
    )
    query = (
        MatchHistory
        .select()
        .where(
            MatchHistory.player_a.is_null(False)
            & MatchHistory.player_b.is_null(False)
            & MatchHistory.match_date.is_null(False)
            & MatchHistory.score.is_null(False)
            # Partido efectivamente terminado -- ver docstring.
            & (MatchHistory.match_date <= yesterday)
            & MatchHistory.id.not_in(histories_with_stats)
        )
    )
    matches, _batch_cursor_id = take_rotating_batch(
        query, MatchHistory.id, after_id=_batch_cursor_id, limit=limit,
        cursor_key="tennis_match_stats",
    )

    processed = resolved = not_found = errors = deferred = 0
    failure_categories = {
        "match_lookup_not_found": 0,
        "source_stats_unavailable": 0,
        "source_stats_incomplete": 0,
        "parser_unrecognized": 0,
        "database_write_failed": 0,
        "exception": 0,
    }
    for match in matches:
        if not enrichment_can_attempt("tennis_match_stats", match.id):
            deferred += 1
            continue
        if has_match_stats_for_history(match.id):
            continue

        processed += 1
        try:
            # La duración y las stats viven en feeds distintos pero usan
            # el mismo ID de Flashscore. Reutilizarlo evita repetir la
            # búsqueda más costosa cuando el otro worker ya la resolvió.
            fs_match_id = (
                match.duration.flashscore_match_id
                if match.duration_id is not None else None
            )
            if fs_match_id is None:
                fs_match_id = match_stats_source_id(match.id)
            if fs_match_id is None:
                tournament_name = match.tournament.tournament_name if match.tournament else None
                fs_match_id = find_match_id(
                    match.player_a.canonical_name, match.player_b.canonical_name,
                    match.match_date, tournament_name, expected_score=match.score,
                )
            if fs_match_id is None:
                not_found += 1
                failure_categories["match_lookup_not_found"] += 1
                record_enrichment_failure("tennis_match_stats", match.id, "match no encontrado")
                continue

            # Resolver nombres/fecha es la parte cara. Conservar el ID aun
            # cuando el proveedor no tenga stats evita repetir esa búsqueda.
            if not remember_match_stats_source(match.id, fs_match_id):
                errors += 1
                failure_categories["database_write_failed"] += 1
                record_enrichment_failure(
                    "tennis_match_stats", match.id,
                    "database_write_failed: no se pudo conservar flashscore_match_id",
                )
                continue

            stats = get_match_statistics(fs_match_id)
            if not stats:
                not_found += 1
                failure_categories["source_stats_unavailable"] += 1
                record_enrichment_source_unavailable(
                    "tennis_match_stats", match.id, "stats no disponibles",
                )
                continue

            status = save_match_stats_for_history_status(match.id, fs_match_id, stats)
            if status == "saved":
                resolved += 1
                clear_enrichment_failure("tennis_match_stats", match.id)
            elif status == "source_incomplete":
                errors += 1
                failure_categories["source_stats_incomplete"] += 1
                record_enrichment_source_unavailable(
                    "tennis_match_stats", match.id, "stats incompletas del proveedor",
                )
            elif status == "already_usable":
                clear_enrichment_failure("tennis_match_stats", match.id)
            else:
                errors += 1
                category = (
                    "parser_unrecognized"
                    if status == "parser_unrecognized"
                    else "database_write_failed"
                )
                failure_categories[category] += 1
                record_enrichment_failure("tennis_match_stats", match.id, status)
        except Exception as e:
            errors += 1
            failure_categories["exception"] += 1
            record_enrichment_failure("tennis_match_stats", match.id, str(e))
            print(f"[enrich_match_stats] error en match_id={match.match_id}: {e}", flush=True)
        time.sleep(_PAUSE_BETWEEN_MATCHES_S)

    result = {
        "processed": processed, "resolved": resolved,
        "not_found": not_found, "errors": errors, "deferred_by_backoff": deferred,
        "failure_categories": failure_categories,
    }
    print(f"[enrich_match_stats] {result}", flush=True)
    return result


if __name__ == "__main__":
    enrich_match_stats()
