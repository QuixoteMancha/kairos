"""Worker periódico: para cada partido de tenis abierto y todavía sin
`winner`, resuelve el mercado real de Kalshi "quién gana el set" activo
(`KXATPSETWINNER`/`KXWTASETWINNER`, ver
`outer_apis/kalshi/markets/tennis_set_winner.py`) y guarda un
`SetProbabilityTick` con su precio real -- 2026-09-01, pedido explícito
del usuario para ampliar Closing Scalp a entradas "saca para ganar UN
SET" (no necesariamente el que cierra el partido), sobre un mercado
confirmado real y tradeable (no un proxy).

Un `Match` puede tener 0, 1, 2 o 3 eventos KXATPSETWINNER a lo largo de
su vida (uno activo a la vez, el del set que se está jugando ahora). El
número de set activo se lee de `LivePointSnapshot.current_set` (el
snapshot más reciente del partido, ya poblado por
`tennis_live_data_worker.py` vía Flashscore) -- NUNCA se re-deriva el
número de set desde otra fuente, ese es el dato canónico ya construido.

Resolución evento-de-set <-> partido: por tour + nombre de jugador
normalizado (igualdad exacta tras normalizar acentos/caso, NO fuzzy --
a diferencia del pairing de ranking en `tennis.py`, acá un mismatch
significaría guardar el precio de OTRO partido, así que se prefiere
fail-closed sobre un match aproximado) + `set_number == current_set`.
Si no se encuentra el evento correspondiente (el set todavía no generó
mercado, ya liquidó y el próximo no abrió todavía, o Kalshi no lista la
serie para ese tour), el partido se saltea ese ciclo -- no se inventa
ni se reutiliza el precio de otro set.

Batching: same `take_rotating_batch` anti-starvation guard as
`tennis_live_data_worker.py` (ver esa nota, mismo patrón de bug
recurrente: un LIMIT fijo sin cursor deja partidos viejos siempre
afuera). Fetch de mercados: UNA llamada por serie (ATP/WTA) por ciclo
via `KalshiAPI.get_markets(series_ticker=...)`, no una por partido."""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

from database.db import init_db_historical
from database.historical.tennis.matchtracking import save_set_probability_tick
from database.historical.tennis.models import LivePointSnapshot, Match
from database.historical.workerstate import (
    mark_cycle_failed, mark_cycle_finished, mark_cycle_started,
    startup_schedule_delay,
)
from outer_apis.kalshi.client import KalshiAPI
from outer_apis.kalshi.markets.tennis_set_winner import (
    build_tennis_set_markets, norm_player_name,
)
from scripts.tennis.rotating_batch import take_rotating_batch

_WORKER_NAME = "tennis_set_tick_worker"
_INTERVAL_S = int(os.getenv("TENNIS_SET_TICK_INTERVAL_SECONDS", "60"))
_BATCH_SIZE = int(os.getenv("TENNIS_SET_TICK_BATCH_SIZE", "40"))

_SERIES_BY_TOUR = {"ATP": "KXATPSETWINNER", "WTA": "KXWTASETWINNER"}


def _current_set(match: Match) -> int | None:
    """Último `current_set` conocido de Flashscore para este partido.
    Fail-closed: None si no hay ningún snapshot todavía o si el más
    reciente no trae número de set."""
    snap = (
        LivePointSnapshot.select()
        .where((LivePointSnapshot.match == match) & LivePointSnapshot.current_set.is_null(False))
        .order_by(LivePointSnapshot.observed_at.desc())
        .first()
    )
    return snap.current_set if snap else None


def _fetch_set_markets_by_tour(api: KalshiAPI, tours: set[str]) -> list[dict]:
    """UNA llamada `get_markets(series_ticker=...)` por tour presente en
    el batch (no una por partido)."""
    markets: list[dict] = []
    for tour in tours:
        series = _SERIES_BY_TOUR.get(tour)
        if series is None:
            continue
        markets.extend(api.get_markets(series_ticker=series))
    return build_tennis_set_markets(markets)


def _resolve_active_set_market(
    match: Match, current_set: int, set_markets: list[dict],
) -> dict | None:
    """Evento de set activo para este partido -- igualdad exacta de
    nombre normalizado (fail-closed, no fuzzy, ver docstring del
    módulo) + mismo tour + mismo número de set. Devuelve el mercado con
    `current_a_price`/`current_b_price` ya orientados a
    `match.player_a_name`/`match.player_b_name` (puede venir invertido
    en el evento real de Kalshi)."""
    target_a = norm_player_name(match.player_a_name)
    target_b = norm_player_name(match.player_b_name)
    for market in set_markets:
        if market["tour"] != match.tour or market["set_number"] != current_set:
            continue
        cand_a = norm_player_name(market["player_a_name"])
        cand_b = norm_player_name(market["player_b_name"])
        if cand_a == target_a and cand_b == target_b:
            return market
        if cand_a == target_b and cand_b == target_a:
            return {
                **market,
                "current_a_price": market["current_b_price"],
                "current_b_price": market["current_a_price"],
            }
    return None


def run_once() -> dict[str, int]:
    init_db_historical()
    cutoff = datetime.now(timezone.utc) - timedelta(days=3)
    query = Match.select().where(
        Match.winner.is_null() & (Match.detected_at >= cutoff)
    )
    matches, _ = take_rotating_batch(
        query, Match.id, after_id=0, limit=_BATCH_SIZE,
        cursor_key="tennis_set_tick_worker",
    )
    result = {
        "open": len(matches), "ticked": 0, "no_current_set": 0,
        "no_matching_event": 0, "errors": 0,
    }
    if not matches:
        return result

    api = KalshiAPI()
    api.initialize()
    tours = {m.tour for m in matches if m.tour}
    try:
        set_markets = _fetch_set_markets_by_tour(api, tours)
    except Exception as exc:
        print(f"[{_WORKER_NAME}] fetch de series falló: {exc}")
        result["errors"] = len(matches)
        return result

    for match in matches:
        try:
            current_set = _current_set(match)
            if current_set is None:
                result["no_current_set"] += 1
                continue
            active_market = _resolve_active_set_market(match, current_set, set_markets)
            if active_market is None:
                result["no_matching_event"] += 1
                continue
            save_set_probability_tick(
                match.id, current_set, active_market["id"],
                active_market["current_a_price"], active_market["current_b_price"],
            )
            result["ticked"] += 1
        except Exception as exc:
            result["errors"] += 1
            print(f"[{_WORKER_NAME}] {match.market_id}: {exc}")

    print(f"[{_WORKER_NAME}] {result}")
    return result


if __name__ == "__main__":
    delay = startup_schedule_delay(_WORKER_NAME, _INTERVAL_S, jitter_max_seconds=60)
    if delay:
        time.sleep(delay)
    while True:
        mark_cycle_started(_WORKER_NAME)
        try:
            cycle_result = run_once()
            mark_cycle_finished(_WORKER_NAME, cycle_result)
        except Exception as exc:
            mark_cycle_failed(_WORKER_NAME, exc)
            print(f"[{_WORKER_NAME}] ciclo falló: {exc}")
        time.sleep(_INTERVAL_S)
