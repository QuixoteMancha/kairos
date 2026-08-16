import os
import time

from engine import Kairos
from database.db import init_db
from database.accounting.systemhaltstate import is_halted

# Si está seteado (ej. en Docker vía KAIROS_SCAN_INTERVAL_SECONDS), corre
# en loop indefinido con este intervalo entre scans -- mismo patrón que el
# `run_main_bg_loop(scan_interval=...)` de la arquitectura pre-"extreme
# makeover" (src/core/orchestration/workers.py, borrado 02d7f35), pero sin
# reconstruir toda esa infraestructura (auto-bets, cross-book, cleanup)
# que ya no aplica a este repo. Sin la env var (uso local/manual por
# defecto), corre una sola vez y termina -- comportamiento sin cambios.
_SCAN_INTERVAL_S = os.getenv("KAIROS_SCAN_INTERVAL_SECONDS")


def _run_once():
    # 2026-08-11, pedido explícito del usuario: "si efectivamente se
    # comprueba que no hay fondos, se deben interrumpir todas las
    # operaciones, para evitar errores por trabajar con datos
    # desactualizados" -- circuit-breaker global (ver
    # database/accounting/systemhaltstate.py, activado por
    # outer_apis/scrapers/brightdata_proxy.py::alert_if_account_broken
    # cuando BrightData confirma un problema de fondos/facturación).
    # Chequeo ANTES de escanear, no después -- nunca genera una
    # recomendación nueva mientras el freno esté activo.
    init_db()
    halted, reason = is_halted()
    if halted:
        msg = f"[kairos] OPERACIONES DETENIDAS (motivo: {reason}) -- ciclo de scan omitido"
        print(msg)
        return {"halted": True, "reason": reason}

    result = Kairos.run_service()
    print(result)
    return result


if __name__ == "__main__":
    if _SCAN_INTERVAL_S:
        interval = int(_SCAN_INTERVAL_S)
        print(f"[kairos] loop mode, scan_interval={interval}s")
        while True:
            try:
                _run_once()
            except Exception as e:
                # Fail-closed para el proceso, no para el scan: un error en
                # un ciclo no debe tumbar el contenedor -- se loggea y se
                # reintenta en el próximo ciclo.
                print(f"[kairos] scan error: {e}")
            time.sleep(interval)
    else:
        _run_once()
