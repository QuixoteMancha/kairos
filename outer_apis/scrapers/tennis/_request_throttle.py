"""Limitador de ventana mínima entre requests, thread-safe -- 2026-08-04,
pedido explícito del usuario: "el fetch de las stats y los nuevos
matches tanto para flashscore como para tennisexplorer, deben tener
pausas para no overhittear tanto los servers de ambos sitios".

Antes de esto, ninguno de los 4 puntos reales de red de tenis en vivo
(`flashscore_scraper.search_player`, `flashscore_scraper._fetch_results_page`,
`flashscore_scraper.get_match_statistics`,
`tennisexplorer_player_scraper._fetch`,
`tennisexplorer_livescore_scraper._fetch_matches_page`) tenía ninguna
pausa -- a diferencia de los scripts de backfill offline (que sí
respetan `_PAUSE_BETWEEN_REQUESTS_S`), el path EN VIVO no tenía
throttle. Esto importa especialmente para
`match_history.py::prefetch_player_histories`, que usa un thread pool
de hasta 16 workers concurrentes -- sin un lock global, cada thread
podría disparar su request casi al mismo tiempo, sin ningún freno real
agregado entre ellos.

Uso: una instancia POR SITIO (no compartida entre Flashscore y
TennisExplorer, son servers distintos con presupuestos distintos),
`.wait()` justo antes de la request real."""

from __future__ import annotations

import threading
import time


class RequestThrottle:
    """Fuerza un intervalo mínimo real entre requests, sin importar
    cuántos threads lo usen concurrentemente (lock global por
    instancia) -- simple pero suficiente para un volumen de tenis en
    vivo (decenas de requests por ciclo, no miles)."""

    def __init__(self, min_interval_s: float):
        self._min_interval_s = min_interval_s
        self._lock = threading.Lock()
        self._last_call_monotonic: float | None = None

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if self._last_call_monotonic is not None:
                elapsed = now - self._last_call_monotonic
                remaining = self._min_interval_s - elapsed
                if remaining > 0:
                    time.sleep(remaining)
            self._last_call_monotonic = time.monotonic()


# Sin cifra oficial de límite confirmada para ninguno de los 2 sitios
# (mismo criterio conservador ya usado en los backfills offline) --
# 0.5s mínimo entre requests reales a cada uno, suficiente para no
# generar ráfagas visibles de bot incluso con el thread pool de
# `prefetch_player_histories` (16 workers) sincronizado por este lock.
flashscore_throttle = RequestThrottle(min_interval_s=0.5)
tennisexplorer_throttle = RequestThrottle(min_interval_s=0.5)
