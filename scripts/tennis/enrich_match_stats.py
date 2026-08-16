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
    has_match_stats_for_history, save_match_stats_for_history,
)
from database.historical.tennis.models import MatchHistory
from outer_apis.scrapers.tennis.flashscore_scraper import (
    find_match_id, get_match_statistics,
)

# Mismo criterio conservador que `enrich_match_durations`: el throttle
# global de Flashscore ya espacia las requests, esto es cortesía extra
# entre partidos (cada partido son 2 requests: resolver id + stats).
_PAUSE_BETWEEN_MATCHES_S = 0.5


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
        )
        .order_by(MatchHistory.id.desc())  # los más recientes primero
        .limit(limit * 3)  # margen: muchos ya tendrán stats y se saltean
    )

    processed = resolved = not_found = errors = 0
    for match in query:
        if processed >= limit:
            break
        if has_match_stats_for_history(match.id):
            continue

        processed += 1
        try:
            tournament_name = match.tournament.tournament_name if match.tournament else None
            fs_match_id = find_match_id(
                match.player_a.canonical_name, match.player_b.canonical_name,
                match.match_date, tournament_name, expected_score=match.score,
            )
            if fs_match_id is None:
                not_found += 1
                continue

            stats = get_match_statistics(fs_match_id)
            if not stats:
                not_found += 1
                continue

            if save_match_stats_for_history(match.id, fs_match_id, stats):
                resolved += 1
            else:
                errors += 1
        except Exception as e:
            errors += 1
            print(f"[enrich_match_stats] error en match_id={match.match_id}: {e}", flush=True)
        time.sleep(_PAUSE_BETWEEN_MATCHES_S)

    result = {
        "processed": processed, "resolved": resolved,
        "not_found": not_found, "errors": errors,
    }
    print(f"[enrich_match_stats] {result}", flush=True)
    return result


if __name__ == "__main__":
    enrich_match_stats()
