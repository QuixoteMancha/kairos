"""Backfill del ranking semanal real WTA -- 2026-08-05, pedido explícito
del usuario ("calcular los resultados de esta estrategia tanto en ATP
como WTA"). Equivalente al backfill de TML-Database para ATP, pero con
un mecanismo distinto: acá el ranking NO viene embebido en cada partido
(TennisExplorer no lo trae), así que se backfillea por separado, por
jugadora, vía la API oficial de WTA.

DOS PASOS:
1. Armar un ROSTER amplio de jugadoras reales -- consultando el ranking
   REAL en varios puntos históricos del rango de evaluación (2016, 2018,
   2020, 2022, 2024, 2026, top 300 cada uno) en vez de solo el top-N de
   HOY, que perdería a cualquier jugadora ya retirada/fuera del circuito
   pero activa durante parte del rango del backtest.
2. Para cada jugadora única del roster, traer su serie completa de
   ranking semanal real (`get_weekly_ranking_history`) y persistirla.

Este roster + ranking semanal cumple una SEGUNDA función además de dar
el ranking real: es el clasificador de "esto es un partido WTA" para
`wta_favorite_surface_winrate_filter_backtest.py` -- un partido de
`TennisMatchHistory` solo se considera WTA real si AMBAS jugadoras
resuelven a una jugadora de este roster (fail-closed, excluye
naturalmente cualquier partido ATP que hubiera quedado mezclado en la
misma tabla)."""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

from database.db import init_db_historical, db_historical
from database.historical.tennis import WtaPlayerRankingHistory
from outer_apis.scrapers.tennis import wta_scraper

_ROSTER_SNAPSHOT_YEARS = [2016, 2018, 2020, 2022, 2024, 2026]
_ROSTER_TOP_N = 300
_PAUSE_BETWEEN_REQUESTS_S = 0.5


def build_roster(years: list[int] = _ROSTER_SNAPSHOT_YEARS, top_n: int = _ROSTER_TOP_N) -> dict[int, str]:
    """{player_id: player_name} real, unión de los top-N de cada
    snapshot histórico -- fail-closed por año (un año que falle no
    aborta el resto)."""
    roster: dict[int, str] = {}
    for year in years:
        players = wta_scraper.get_ranked_players_at(f"{year}-06-01", n=top_n)
        for p in players:
            roster[p["id"]] = p["name"]
        time.sleep(_PAUSE_BETWEEN_REQUESTS_S)
    return roster


def backfill_player_ranking(player_id: int, player_name: str) -> int:
    """Persiste la serie semanal real de una jugadora -- idempotente
    (reemplaza su serie completa, para poder refrescar sin duplicar).
    Devuelve el número de entradas guardadas."""
    init_db_historical()
    history = wta_scraper.get_weekly_ranking_history(player_id)
    now = datetime.now(timezone.utc)

    with db_historical.atomic():
        WtaPlayerRankingHistory.delete().where(WtaPlayerRankingHistory.player_id == player_id).execute()
        if not history:
            return 0
        rows = [{
            "player_id": player_id, "player_name": player_name,
            "ranked_at": h["ranked_at"], "singles_ranking": h["singles_ranking"], "fetched_at": now,
        } for h in history]
        WtaPlayerRankingHistory.insert_many(rows).on_conflict_ignore().execute()
    return len(history)


def run_backfill(years: list[int] = _ROSTER_SNAPSHOT_YEARS, top_n: int = _ROSTER_TOP_N) -> dict:
    roster = build_roster(years, top_n)
    print(f"[wta_player_ranking_backfill] roster real: {len(roster)} jugadoras únicas", flush=True)

    ok = failed = 0
    total_entries = 0
    for i, (player_id, name) in enumerate(roster.items(), start=1):
        try:
            n = backfill_player_ranking(player_id, name)
            ok += 1
            total_entries += n
            print(f"[wta_player_ranking_backfill] ({i}/{len(roster)}) {name} -> {n} entradas semanales", flush=True)
        except Exception as e:
            failed += 1
            print(f"[wta_player_ranking_backfill] ({i}/{len(roster)}) {name} -> ERROR {e}", flush=True)
        time.sleep(_PAUSE_BETWEEN_REQUESTS_S)

    result = {"roster_size": len(roster), "ok": ok, "failed": failed, "total_ranking_entries": total_entries}
    print(f"[wta_player_ranking_backfill] TERMINADO: {result}", flush=True)
    return result


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else _ROSTER_TOP_N
    run_backfill(top_n=n)
