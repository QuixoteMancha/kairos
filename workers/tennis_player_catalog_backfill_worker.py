"""Worker persistente: backfillea el catálogo COMPLETO de jugadores de
TennisExplorer (todos los países -> paginación -> cada jugador) contra
`MatchHistory`/`Player`/`TennisHistoricalOdds` -- 2026-08-16, pedido
explícito del usuario: "un worker que tenga threads para backfillear
los players que corra constantemente".

Contexto (ver sesión 2026-08-16, revisión de escala + fixes de
`writer.py` antes de este worker): el catálogo completo son decenas de
miles de jugadores reales -- una sola pasada corre en el orden de días,
no minutos, incluso con threads. Este worker está diseñado para ESO:
corre en background de forma indefinida (`restart: unless-stopped` en
docker-compose), cada pasada completa es idempotente (no duplica nada
ya guardado, ver `backfill_player`/`save_match_detail`), así que
re-lanzarla periódicamente solo completa lo que falte (jugadores nuevos
en el catálogo, partidos nuevos jugados) sin costo extra sobre lo que
ya está.

`TENNIS_CATALOG_BACKFILL_MAX_WORKERS` (default 4): threads en paralelo
para el trabajo pesado por jugador (11 años de historial + resolución
de partidos) -- el descubrimiento del catálogo (países/paginación)
sigue siendo secuencial a propósito (generador con estado BFS
compartido, ver `run_country_catalog_backfill`). Seguro bajo
concurrencia real: throttle global compartido de TennisExplorer
(`tennisexplorer_throttle`), Postgres con manejo de conflictos real
(`writer.py::_resolve_player_id`/`_resolve_tournament_id`/
`save_match_detail` ya recuperan correctamente ante una carrera de
`IntegrityError` entre threads -- fix de esta misma sesión, verificado
en vivo con 8 threads concurrentes antes de construir este worker).

`TENNIS_CATALOG_BACKFILL_INTERVAL_SECONDS` (default 3600 = 1h): pausa
ENTRE pasadas completas del catálogo, no dentro de una pasada -- dado
que una pasada real tarda mucho más que este intervalo, en la práctica
el worker queda corriendo casi todo el tiempo; el intervalo solo importa
para el caso (poco común) de que una pasada termine rápido (catálogo ya
casi completo, quedan pocos jugadores nuevos)."""

import os
import time

from database.db import init_db_historical
from database.historical.workerstate import mark_cycle_failed, mark_cycle_finished, mark_cycle_started
from scripts.tennis.wta_top200_historical_backfill import run_country_catalog_backfill

_MAX_WORKERS = int(os.getenv("TENNIS_CATALOG_BACKFILL_MAX_WORKERS", "4"))
_INTERVAL_S = int(os.getenv("TENNIS_CATALOG_BACKFILL_INTERVAL_SECONDS", "3600"))
_MAX_PLAYERS = os.getenv("TENNIS_CATALOG_BACKFILL_MAX_PLAYERS")
_WORKER_NAME = "tennis_player_catalog_backfill_worker"


def _run_once() -> dict:
    max_players = int(_MAX_PLAYERS) if _MAX_PLAYERS and _MAX_PLAYERS.strip() else None
    result = run_country_catalog_backfill(max_players=max_players, max_workers=_MAX_WORKERS)
    print(f"[tennis_player_catalog_backfill] {result}", flush=True)
    return result


if __name__ == "__main__":
    init_db_historical()
    print(
        f"[tennis_player_catalog_backfill] loop mode, "
        f"max_workers={_MAX_WORKERS}, interval_between_passes={_INTERVAL_S}s",
        flush=True,
    )
    while True:
        try:
            mark_cycle_started(_WORKER_NAME)
            result = _run_once()
            mark_cycle_finished(_WORKER_NAME, result)
        except Exception as e:
            mark_cycle_failed(_WORKER_NAME, e)
            print(f"[tennis_player_catalog_backfill] error: {e}", flush=True)
        time.sleep(_INTERVAL_S)
