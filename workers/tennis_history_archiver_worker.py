"""Worker periódico: monitorea el tamaño de la DB histórica y archiva la
mitad más vieja de los partidos de tenis al disco externo cuando
corresponde -- ver `database/historical/tennis/archiver.py` para el
razonamiento completo (pedido explícito del usuario 2026-07-31).

Intervalo por default más largo que los otros workers (1h, no 60s): el
chequeo de tamaño de disco es barato pero no necesita alta frecuencia --
la DB no puede crecer lo bastante rápido como para justificar chequear
cada minuto."""

import os
import time

from database.db import init_db_historical
from database.historical.workerstate import mark_cycle_failed, mark_cycle_finished, mark_cycle_started
from database.historical.tennis.archiver import run_archiver_cycle

_REFRESH_INTERVAL_S = os.getenv("TENNIS_ARCHIVER_INTERVAL_SECONDS")
_DEFAULT_INTERVAL_S = 3600  # 1h
_WORKER_NAME = "tennis_history_archiver_worker"


def _run_once() -> dict:
    result = run_archiver_cycle()
    print(f"[tennis_history_archiver] {result}")
    return result


if __name__ == "__main__":
    init_db_historical()
    if _REFRESH_INTERVAL_S:
        interval = int(_REFRESH_INTERVAL_S) if _REFRESH_INTERVAL_S.strip() else _DEFAULT_INTERVAL_S
        print(f"[tennis_history_archiver] loop mode, refresh_interval={interval}s")
        while True:
            try:
                mark_cycle_started(_WORKER_NAME)
                result = _run_once()
                mark_cycle_finished(_WORKER_NAME, result)
            except Exception as e:
                mark_cycle_failed(_WORKER_NAME, e)
                print(f"[tennis_history_archiver] error: {e}")
            time.sleep(interval)
    else:
        _run_once()
