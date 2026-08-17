"""Worker REACTIVO de relleno de huecos de datos -- 2026-08-16, pedido
explícito del usuario: "que sea reactivo ese worker, que escanee qué
datos faltan y en base a los fetch que tenemos vaya llenando los datos
que faltan", con la restricción dura de que "no podemos reiniciar
nuevamente" (nada acá rehace el backfill: todo es incremental sobre lo
que YA está en la base).

Cada ciclo escanea el estado real de los huecos y corre los pasos en
orden de COSTO CRECIENTE, cada uno acotado y fail-closed por separado
(un paso que falla no frena a los siguientes ni tumba el worker):

  1. `player_ages` -- SIN RED, puro cálculo (`Player.birthdate` +
     `match_date` -> `winner_age`/`loser_age`). Barato, tope alto.
  2. Bio, torneos, duraciones, estadísticas y Elo corren EN PARALELO
     en contenedores independientes; no esperan este ciclo.

El nivel EXACTO (500 vs 250) SÍ se resuelve, vía
`tournament_levels_refined` -> página de torneo de TennisExplorer
(puntos de ranking del ganador). El genérico "ATP Tour"/"WTA Tour" que
pone el paso sin-red es solo un estado transitorio hasta que ese paso
lo refine.

El único hueco que sigue SIN fuente es `MatchHistory.best_of`/`minutes`
(columnas legacy) -- no se inventan. Ver `scripts/tennis/fill_data_gaps.py`.

Env vars:
  `TENNIS_GAP_FILLER_INTERVAL_SECONDS` (default 3600 = 1h)
  `TENNIS_GAP_FILLER_AGES_LIMIT`       (default 2000, sin red)
"""

import os
import time

from database.db import init_db_historical
from database.historical.workerstate import mark_cycle_failed, mark_cycle_finished, mark_cycle_started, wait_for_startup_schedule
from scripts.tennis.fill_data_gaps import count_data_gaps, fill_player_ages

_INTERVAL_S = int(os.getenv("TENNIS_GAP_FILLER_INTERVAL_SECONDS", "3600"))
_AGES_LIMIT = int(os.getenv("TENNIS_GAP_FILLER_AGES_LIMIT", "2000"))
_WORKER_NAME = "tennis_data_gap_filler_worker"


def _step(name: str, fn, *args, **kwargs) -> dict:
    """Corre un paso aislado -- fail-closed: cualquier excepción se
    reporta y se sigue con el resto (mismo criterio que
    `initial_tennis_backfill._run_step`)."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _run_once(cycle: int = 0) -> dict:
    result = {"gaps_before": _step("gaps", count_data_gaps)}

    # 1. Edades -- sin red, barato.
    result["player_ages"] = _step("player_ages", fill_player_ages, limit=_AGES_LIMIT)

    print(f"[tennis_gap_filler] {result}", flush=True)
    return result


if __name__ == "__main__":
    init_db_historical()
    wait_for_startup_schedule(_WORKER_NAME, _INTERVAL_S, jitter_max_seconds=120)
    print(
        f"[tennis_gap_filler] loop mode, interval={_INTERVAL_S}s, "
        f"ages_limit={_AGES_LIMIT}",
        flush=True,
    )
    cycle = 0
    while True:
        try:
            mark_cycle_started(_WORKER_NAME)
            result = _run_once(cycle)
            mark_cycle_finished(_WORKER_NAME, result)
        except Exception as e:
            mark_cycle_failed(_WORKER_NAME, e)
            print(f"[tennis_gap_filler] error: {e}", flush=True)
        cycle += 1
        time.sleep(_INTERVAL_S)
