"""
Backtest de "apostar siempre al favorito" usando el ranking REAL que
cada jugador tenía AL MOMENTO del partido (no el ranking de hoy, a
diferencia de `scripts/favorite_strategy_backtest.py`) -- pedido
explícito del usuario 2026-08-02, resuelto vía
`outer_apis/scrapers/tennis/tml_database_scraper.py::TML-Database`
(registro COMPLETO del tour ATP, no sesgado, ver docstring de ese
módulo).

ALCANCE: SOLO ATP (sin equivalente WTA del mismo autor, investigado).
Torneos "250 en adelante" (`TOUR_LEVELS_250_PLUS`: 250/500/Masters/
Slam/Finals -- excluye Davis Cup y exhibición).

Ya no hace falta el caveat de "ranking actual como proxy" de los
backtests anteriores (`favorite_strategy_backtest.py`,
`sf_final_hypothesis_validation.py`) -- acá el ranking es real y
exacto para cada partido individual."""

from __future__ import annotations

import json
from typing import Any

from outer_apis.scrapers.tennis.tml_database_scraper import get_matches_range, TOUR_LEVELS_250_PLUS

_DEFAULT_START_YEAR = 2016  # ~10 años atrás, mismo alcance usado en el resto de la sesión
_DEFAULT_END_YEAR = 2026


def run_backtest(start_year: int = _DEFAULT_START_YEAR, end_year: int = _DEFAULT_END_YEAR) -> dict[str, Any]:
    matches = get_matches_range(start_year, end_year, levels=TOUR_LEVELS_250_PLUS)

    cases = []
    excluded_no_rank = 0
    for m in matches:
        if m["winner_rank"] is None or m["loser_rank"] is None:
            excluded_no_rank += 1
            continue
        favorite_rank = min(m["winner_rank"], m["loser_rank"])
        underdog_rank = max(m["winner_rank"], m["loser_rank"])
        hit = m["winner_rank"] < m["loser_rank"]  # ganó quien tenía MEJOR ranking real
        cases.append({
            "tourney_name": m["tourney_name"], "level": m["level"], "date": m["date"],
            "round": m["round"], "favorite_rank": favorite_rank, "underdog_rank": underdog_rank,
            "hit": hit,
        })

    n = len(cases)
    hits = sum(1 for c in cases if c["hit"])

    by_level: dict[str, dict[str, Any]] = {}
    for level in sorted({c["level"] for c in cases}):
        group = [c for c in cases if c["level"] == level]
        g_hits = sum(1 for c in group if c["hit"])
        by_level[level] = {"n": len(group), "hits": g_hits, "hit_rate": round(g_hits / len(group), 4)}

    by_round: dict[str, dict[str, Any]] = {}
    for round_name in sorted({c["round"] for c in cases}):
        group = [c for c in cases if c["round"] == round_name]
        g_hits = sum(1 for c in group if c["hit"])
        by_round[round_name] = {"n": len(group), "hits": g_hits, "hit_rate": round(g_hits / len(group), 4)}

    return {
        "years": f"{start_year}-{end_year}", "total_matches_fetched": len(matches),
        "excluded_no_rank": excluded_no_rank, "evaluable_cases": n, "hits": hits,
        "overall_hit_rate": round(hits / n, 4) if n else None,
        "by_level": by_level, "by_round": by_round,
        "caveat": "SOLO ATP -- sin equivalente WTA del mismo autor. Ranking REAL al momento del partido, no aproximado.",
    }


if __name__ == "__main__":
    print(json.dumps(run_backtest(), indent=2, ensure_ascii=False))
