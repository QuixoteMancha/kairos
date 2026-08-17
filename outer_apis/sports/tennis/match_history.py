"""
Public call surface for real tennis match-history signals (recent form +
head-to-head) — wraps `outer_apis/scrapers/tennis/tennisexplorer_player_scraper.py`.

MIGRADO DE FLASHSCORE A TENNISEXPLORER (2026-08-04, pedido explícito
del usuario: "elimina cualquier referencia a flashscore... veo que
tennisexplorer se actualiza rápido"). Antes usaba
`flashscore_scraper.get_player_recent_matches` (requiere proxy
residencial BrightData, costo real ~$1/día + incidentes de saldo
agotado documentados). `tennisexplorer_player_scraper.get_player_recent_matches`
NO necesita proxy y devuelve el MISMO shape de dict
({"fecha","oponente","resultado","score","source"}), así que todo lo
de abajo (caching, prefetch paralelo, fail-closed) sigue igual sin
tocar la lógica de negocio.

Flashscore queda reservado SOLO para lo que no tiene reemplazo:
`flashscore_scraper.find_match_id`/`get_match_statistics` (estadísticas
detalladas de partido -- aces/winners/unforced errors -- que
TennisExplorer no expone), usado por `tennis_match_tick_worker.py` --
esas llamadas son escasas (una vez por partido liquidado, no un
prefetch masivo por scan), así que el costo/riesgo de proxy que queda
es mucho menor.

Separation of concerns, same pattern as `outer_apis/tennis_rankings.py` and
`outer_apis/tennis_serve_stats.py`: `outer_apis/scrapers/tennis/*.py` only does raw
HTTP + parsing, this file owns the call methods, fail-closed error
handling, and caching.

Purpose: this exists specifically to give `TennisFModel` a signal beyond
pure rank-bucket (see kairos-tennis-critical-bugs-and-model-risk memory —
a rank-only model disagreed with a real market by 40 points on a case
where the market almost certainly knew about a player's recent
form/injury that rank alone doesn't capture). Recent win-rate and
head-to-head are still simple signals, not a full replacement for
form/injury/surface modeling, but they're REAL data instead of nothing.

PARALLEL PREFETCH (added 2026-07-20, see kairos-tennis-flashscore-
worker-parallelization memory -- name kept for history, source is now
TennisExplorer): unlike the ranking/serve-stat sources, neither
Flashscore nor TennisExplorer has a bulk endpoint — each player needs
its own request. Running that sequentially for every tennis market in a
real scan (~100 unique players) took over 5 minutes and blew past a
reasonable timeout. This mirrors the pre-makeover architecture's worker pattern
(`src/core/orchestration/workers.py`, deleted `02d7f35` -- periodic
background workers that pre-warmed data caches so the hot scan path never
blocked on live fetches) adapted to this repo's CURRENT one-shot
execution model (no long-running daemon exists yet, so there's no
persistent background thread to host a periodic worker in) -- what's
rescued here is the PARALLELIZATION half of that pattern: a thread pool
that fetches all the players needed for one scan pass concurrently,
BEFORE `TennisFModel` needs them, so the per-market fair-price
calculation hits an already-warm cache instead of blocking on a live
proxy fetch one player at a time. `engine.py` calls `prefetch_player_histories`
once per scan with every tennis market's player names, before building
`LimitArb`.

CACHING (rediseñado 2026-07-28, ver memoria
kairos-tennis-brightdata-db-cache-2026-07-28): antes había un dict en
memoria (`_recent_matches_cache`) ADEMÁS de la persistencia en DB
(`save_match_history`). Ese cache en memoria era el ÚNICO gate contra un
fetch en vivo -- vivía solo en RAM del proceso, así que cualquier
reinicio del contenedor `kairos` lo vaciaba por completo y disparaba un
refetch masivo vía BrightData de TODOS los jugadores activos de una sola
vez (causa raíz real de un pico de tráfico de proxy investigado el mismo
día). La DB ya se escribía en cada fetch fresco pero nunca se leía de
vuelta -- pura contabilidad sin uso funcional.

Fix: se elimina el dict en memoria. Ahora CADA llamada consulta
`database.historical.tennis.writer.get_recent_match_history()` primero
(SQLite en WAL + índice por `player_name`, del orden de milisegundos,
nada comparable a los ~5s de un fetch real vía proxy) -- si hay datos
con menos de 1h de antigüedad, se usan directo, sin red. Solo si la DB
no tiene nada fresco se hace el fetch en vivo (y ese resultado se
persiste, cerrando el círculo). La DB es ahora la única fuente de verdad,
sobrevive a cualquier reinicio.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

# Import conservado como superficie de compatibilidad para callers/tests que
# verifican que esta ruta de consulta NO toca el scraper.
from outer_apis.scrapers.tennis import tennisexplorer_player_scraper
from database.historical.tennis.queries import (
    get_head_to_head as query_head_to_head,
    get_player_matches,
)

# Allowlist de fetch en vivo (2026-08-01, pedido explícito del usuario):
# solo hacer fetch de Flashscore para jugadores de partidos NUEVOS
# detectados vía la API de Kalshi en el scan actual (ver
# engine.py::run_service, database/historical/tennis/matchtracking.py::
# filter_new_markets para el trade-off completo). `None` = sin
# restricción (comportamiento de siempre, usado también por defecto en
# todos los tests existentes que no configuran la allowlist). Un jugador
# que NO está en la allowlist nunca toca la red -- se sirve lo último que
# haya en DB aunque tenga días, o [] si nunca se fetcheó nada de él/ella."""
_fetch_allowlist: set[str] | None = None


def set_fetch_allowlist(names: set[str] | None) -> None:
    """`None` desactiva la restricción (fetch libre, como antes de
    2026-08-01). Un `set` (incluso vacío) restringe el fetch en vivo
    exclusivamente a esos nombres -- pensado para llamarse UNA vez por
    ciclo de scan, con los jugadores de los partidos nuevos detectados
    ese ciclo."""
    global _fetch_allowlist
    _fetch_allowlist = names


# 2026-07-31 (cambio de diseño explícito del usuario): la frescura de
# `get_recent_match_history` ya NO es una ventana rodante de horas -- es
# un borde de calendario UTC (00:00 GMT), ver
# database/historical/tennis/writer.py::_last_midnight_utc para el
# razonamiento completo. `max_age_hours=None` (default de la función)
# usa ese borde diario; no se pasa nada explícito acá porque el borde
# diario YA es el comportamiento correcto para este caller.

# Single canonical fetch depth per player: get_recent_form/get_head_to_head
# both slice from this ONE cached list instead of each doing their own
# separately-cached fetch at a different depth (that used to double the
# number of live proxy requests per player -- one at n=10 for form, one at
# n=40 for h2h -- for no benefit, since a deeper fetch trivially covers a
# shallower request).
_MAX_DEPTH = 40

MIN_MATCHES_FOR_FORM = 3  # fail-closed: fewer than this -> no form signal, not a fabricated one.
# Tuned empirically 2026-07-20: throughput roughly doubled from 8->16
# workers (~0.58 -> ~1.7 players/s), flattened out from 16->24 (proxy/
# network-bound past that point, not thread-count-bound) -- 16 is a
# reasonable balance between speed and not hammering TennisExplorer with
# too many simultaneous requests (no proxy/session limit here anymore, but
# still real courtesy against a site that has no official rate-limit docs).
DEFAULT_PREFETCH_WORKERS = 16


def _fetch_and_cache(player_name: str) -> list[dict]:
    return get_player_matches(player_name, limit=_MAX_DEPTH)


def prefetch_player_histories(names: list[str], max_workers: int = DEFAULT_PREFETCH_WORKERS) -> None:
    """Warms the cache for every name in `names` IN PARALLEL via a thread
    pool, instead of one sequential proxy fetch per player. Call this
    once per scan (e.g. from engine.py, with every tennis market's player
    names) BEFORE any TennisFModel.calculate_fair_price call, so the
    per-market fair-price computation hits an already-warm cache.

    Tolerant per player: a single player's fetch failing/timing out
    doesn't stop the others (each `_fetch_and_cache` call is already
    internally fail-closed to [], see tennisexplorer_player_scraper docstring) --
    this function itself never raises.
    """
    unique_names = sorted({n.strip() for n in names if n and n.strip()})
    if not unique_names:
        return

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch_and_cache, name): name for name in unique_names}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception:
                pass  # per-player fetch is already fail-closed; never let one failure propagate here.


def get_recent_matches(player_name: str, n: int = 20) -> list[dict]:
    """Cached wrapper around the raw scraper (or the prefetch warm cache,
    if `prefetch_player_histories` already ran for this player). Fail-closed:
    [] on any failure or unknown player."""
    matches = _fetch_and_cache(player_name)
    return matches[:n]


def get_recent_form(player_name: str, n: int = 10) -> dict[str, Any] | None:
    """Recent win rate over the player's last `n` charted matches.

    Returns:
        {"win_rate": float in [0,1], "n_matches": int} or None if fewer
        than `MIN_MATCHES_FOR_FORM` real matches were found (fail-closed:
        no fabricated form signal from too little data).
    """
    matches = get_recent_matches(player_name, n=n)
    if len(matches) < MIN_MATCHES_FOR_FORM:
        return None
    wins = sum(1 for m in matches if m.get("resultado") == "W")
    return {"win_rate": round(wins / len(matches), 4), "n_matches": len(matches)}


def get_head_to_head(player_a: str, player_b: str, lookback: int = 40) -> dict[str, Any] | None:
    """Head-to-head record between two players, derived from player_a's
    recent match history (filtering opponents matching player_b's name).

    Args:
        lookback: how many of player_a's recent matches to scan for
            meetings with player_b (capped at `_MAX_DEPTH`, the canonical
            fetch depth -- deeper values don't fetch more, they just scan
            less of what's already cached).

    Returns:
        {"a_wins": int, "b_wins": int, "n_matches": int} or None if no
        head-to-head matches were found in the scanned window (fail-closed:
        distinct from "0-0" wins, which would misleadingly suggest they've
        played and split evenly -- None means "no data", not "even record").
    """
    return query_head_to_head(player_a, player_b, limit=lookback)
