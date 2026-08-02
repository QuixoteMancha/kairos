"""Integración real de `strategies/set1_favorite_reaction.py` con el
score en vivo (`outer_apis/scrapers/tennis/tennisexplorer_livescore_scraper.py`)
-- 2026-08-02, pedido explícito del usuario: "integración con la
estrategia set_1_favorite_reaction".

Antes de este worker, "el favorito ganó el set 1" era una condición que
el usuario confirmaba a mano (caso real: Pegula vs Eala). Este worker
la detecta sola: para cada partido de Kalshi todavía abierto
(`TennisScannedMatch`, ver `get_open_scanned_matches`), busca el score
real en vivo, determina el favorito por RANKING ACTUAL (mismo criterio
usado en la sesión para Pegula/Eala -- no por precio de mercado, ver
caveat de `favorite_by_historical_rank_backtest.py`), y si el set 1 ya
terminó, guarda la recomendación de `recommend_set1_entry` -- idempotente
por `market_id`, nunca se recalcula un partido ya registrado.

SOLO REGISTRO -- ninguna orden se coloca, línea roja del proyecto sigue
vigente. El usuario sigue siendo quien decide operar o no la
recomendación."""

from __future__ import annotations

import os
import time

from database.db import init_db
from database.accounting.set1entrysignal import Set1EntrySignal
from database.historical.tennis.matchtracking import get_open_scanned_matches
from outer_apis.kalshi.markets.tennis import _build_name_to_rank, _lookup_rank
from outer_apis.scrapers.tennis.tennisexplorer_livescore_scraper import get_live_matches, get_set1_winner
from strategies.set1_favorite_reaction import recommend_set1_entry

_REFRESH_INTERVAL_S = os.getenv("SET1_REACTION_INTERVAL_SECONDS")
_DEFAULT_INTERVAL_S = 60


def _find_live_match(player_a: str, player_b: str, tour: str, live_cache: dict) -> dict | None:
    if tour not in live_cache:
        live_cache[tour] = get_live_matches(tour)
    for m in live_cache[tour]:
        if {m["player_a"], m["player_b"]} == {player_a, player_b}:
            return m
    return None


def run_once() -> dict:
    init_db()
    open_matches = get_open_scanned_matches()
    if not open_matches:
        return {"open": 0, "processed": 0, "skipped": 0, "errors": 0}

    rank_cache: dict[str, dict[str, int]] = {}
    live_cache: dict[str, list[dict]] = {}

    processed = skipped = errors = 0
    for match in open_matches:
        market_id = match["market_id"]
        try:
            if Set1EntrySignal.select().where(Set1EntrySignal.market_id == market_id).exists():
                skipped += 1
                continue

            tour = match["tour"]
            name_a, name_b = match["player_a_name"], match["player_b_name"]

            live_match = _find_live_match(name_a, name_b, tour, live_cache)
            if live_match is None:
                skipped += 1
                continue  # partido todavía no arrancó, o no está en la fuente de score en vivo

            set1_winner_side = get_set1_winner(name_a, name_b, tour)
            if set1_winner_side is None:
                skipped += 1
                continue  # set 1 todavía en curso -- fail-closed, no se adivina

            if tour not in rank_cache:
                rank_cache[tour] = _build_name_to_rank(tour)
            rank_a = _lookup_rank(name_a, rank_cache[tour])
            rank_b = _lookup_rank(name_b, rank_cache[tour])
            if rank_a is None or rank_b is None:
                skipped += 1
                continue  # sin ranking real de alguno de los 2 -- fail-closed

            favorite_is_a = rank_a < rank_b
            favorite_name = name_a if favorite_is_a else name_b
            underdog_name = name_b if favorite_is_a else name_a
            favorite_rank = min(rank_a, rank_b)
            underdog_rank = max(rank_a, rank_b)
            rank_gap = underdog_rank - favorite_rank

            favorite_side = "a" if favorite_is_a else "b"
            favorite_won_set1 = (set1_winner_side == favorite_side)

            recommendation = recommend_set1_entry(favorite_won_set1, rank_gap)

            Set1EntrySignal.create(
                market_id=market_id, favorite_name=favorite_name, underdog_name=underdog_name,
                favorite_rank=favorite_rank, underdog_rank=underdog_rank, rank_gap=rank_gap,
                favorite_won_set1=favorite_won_set1, size_pct=recommendation["size_pct"],
                favorite_win_rate_basis=recommendation.get("favorite_win_rate_basis"),
            )
            processed += 1
            print(f"[set1_reaction] {market_id}: {recommendation}")
        except Exception as e:
            errors += 1
            print(f"[set1_reaction] error en {market_id}: {e}")

    result = {"open": len(open_matches), "processed": processed, "skipped": skipped, "errors": errors}
    print(f"[set1_reaction] {result}")
    return result


if __name__ == "__main__":
    if _REFRESH_INTERVAL_S:
        interval = int(_REFRESH_INTERVAL_S) if _REFRESH_INTERVAL_S.strip() else _DEFAULT_INTERVAL_S
        print(f"[set1_reaction] loop mode, refresh_interval={interval}s")
        while True:
            try:
                run_once()
            except Exception as e:
                print(f"[set1_reaction] ciclo con error: {e}")
            time.sleep(interval)
    else:
        run_once()
