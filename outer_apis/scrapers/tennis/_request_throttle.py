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
# 2026-08-09, pedido explícito del usuario: bajar el piso de
# TennisExplorer de 0.5s a 0.3s (~3.3 req/s techo) para acelerar el
# vaciado de la cola de 318K candidatos (`TennisMatchHistoryUnvalidated`)
# -- no hay cifra oficial de límite de ninguno de los 2 sitios, este es
# un ajuste puntual del usuario, no una re-evaluación de riesgo.
tennisexplorer_throttle = RequestThrottle(min_interval_s=0.3)
# 2026-08-10, pedido explícito del usuario: verificar sexo/género de
# jugadores contra Wikidata en vez de seguir hiteando TennisExplorer.
# Wikidata NO publica un límite numérico duro para su API REST/Action
# estándar -- la etiqueta oficial pide requests SERIALES (no paralelos)
# + User-Agent identificable + backoff ante 429 (ver
# https://www.wikidata.org/wiki/Wikidata:Data_access). El servicio
# SPARQL (el que usamos, 1 query combinada por jugador) sí publica un
# límite explícito: 60s de tiempo de query por minuto por IP (ráfaga
# 120s/min), máx 5 concurrentes (ver
# https://www.wikidata.org/wiki/Wikidata:SPARQL_query_service/query_limits).
# 1s mínimo entre requests (1 req/s) es MUY conservador frente a ese
# presupuesto real.
wikidata_throttle = RequestThrottle(min_interval_s=1.0)
