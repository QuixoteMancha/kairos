"""Worker periódico: puebla el clima real de los partidos de tenis --
2026-08-16, pedido explícito del usuario: "agregar un worker que haga
fetch de los datos climáticos, que revise cada 1 hora y haga fetch de
los datos climáticos de los matches".

Cubre las DOS tablas de partidos, cada una con su propio criterio real
(ver `outer_apis/sports/tennis/match_weather.py` para el detalle):

  1. `MatchHistory` (backfill histórico de TennisExplorer, lo que llena
     el `tennis_player_catalog_backfill_worker`): 1 fila de clima por
     partido, AGREGADO DEL DÍA (fase `match_day`) -- estos partidos
     tienen fecha pero NO hora de inicio, así que no se fabrican fases
     horarias. Este es el gap real que este worker cierra:
     `MatchWeather.match_history` existía como FK desde 2026-08-09 pero
     no tenía NINGÚN productor.

  2. `TennisScannedMatch` (tracker vivo de Kalshi): las 3 fases reales
     (before/during/end) basadas en timestamps reales
     (`detected_at`/ticks/`settled_at`) -- path que ya existía, se
     mantiene sin cambios, ahora corre periódicamente en vez de solo a
     mano.

Ambos pasos son idempotentes (índices únicos reales `(match, phase)` y
`(match_history, phase)` + chequeo previo) -- correr esto cada hora no
duplica nada, solo completa lo que falte.

Cupo de Open-Meteo: el rate limiter real compartido
(`open_meteo.throttle_sleep`, límites free tier 600/min - 5000/h -
10000/día con margen) ya frena solo; si igual se agota el cupo diario,
el paso corta limpio (`budget_exhausted: True`) en vez de seguir
pegándole a la API, y reintenta en la corrida siguiente.

`TENNIS_WEATHER_INTERVAL_SECONDS` (default 3600 = 1h, pedido explícito).
`TENNIS_WEATHER_BATCH_LIMIT` (default 500): partidos de `MatchHistory`
por corrida -- el histórico completo son decenas de miles, se completa
de a tandas horarias respetando el cupo real de la API."""

import os
import time

from database.db import init_db_historical
from database.historical.workerstate import mark_cycle_failed, mark_cycle_finished, mark_cycle_started
from outer_apis.sports.tennis.match_weather import (
    populate_weather_for_all_resolved_matches,
    populate_weather_for_match_history,
)

_INTERVAL_S = int(os.getenv("TENNIS_WEATHER_INTERVAL_SECONDS", "3600"))
_BATCH_LIMIT = int(os.getenv("TENNIS_WEATHER_BATCH_LIMIT", "500"))
_WORKER_NAME = "tennis_weather_worker"


def _run_once() -> dict:
    result = {}

    # 1. MatchHistory (histórico backfilleado) -- el volumen real.
    try:
        result["match_history"] = populate_weather_for_match_history(limit=_BATCH_LIMIT)
    except Exception as e:
        result["match_history"] = {"error": f"{type(e).__name__}: {e}"}

    # 2. TennisScannedMatch (partidos vivos de Kalshi ya resueltos) --
    # fail-closed independiente: un fallo acá no debe impedir el paso 1
    # ni tumbar el worker.
    try:
        result["scanned_matches"] = populate_weather_for_all_resolved_matches()
    except Exception as e:
        result["scanned_matches"] = {"error": f"{type(e).__name__}: {e}"}

    print(f"[tennis_weather] {result}", flush=True)
    return result


if __name__ == "__main__":
    init_db_historical()
    print(
        f"[tennis_weather] loop mode, interval={_INTERVAL_S}s, batch_limit={_BATCH_LIMIT}",
        flush=True,
    )
    while True:
        try:
            mark_cycle_started(_WORKER_NAME)
            result = _run_once()
            mark_cycle_finished(_WORKER_NAME, result)
        except Exception as e:
            mark_cycle_failed(_WORKER_NAME, e)
            print(f"[tennis_weather] error: {e}", flush=True)
        time.sleep(_INTERVAL_S)
