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


def parse_set1_winner(score: str | None) -> str | None:
    """'winner' | 'loser' | None -- quién de los dos (referido al
    GANADOR REAL del partido) se llevó el primer set, parseando el
    campo `score` real de TML-Database (2026-08-02, pedido explícito
    del usuario: "cuál es el winrate del favorito al set 1").

    Convención real confirmada en vivo (misma que Match Charting
    Project): el score SIEMPRE lista las games del GANADOR DEL PARTIDO
    primero en cada set -- ej. '0-6 6-4 6-4' significa que el ganador
    del partido PERDIÓ el primer set 0-6 antes de remontar. NO asume
    que quien ganó el partido ganó el primer set.

    Fail-closed: None si `score` está vacío, es un walkover/retiro sin
    set 1 jugable ('W/O'), o el primer token no matchea el patrón
    'N-M' (con o sin tiebreak entre paréntesis) -- nunca adivina."""
    if not score:
        return None
    first_set = score.strip().split(" ")[0]
    if not first_set or first_set.upper() in ("W/O", "WO"):
        return None
    import re
    m = re.match(r"^(\d+)-(\d+)", first_set)
    if not m:
        return None
    winner_games, loser_games = int(m.group(1)), int(m.group(2))
    if winner_games == loser_games:
        return None  # dato inconsistente, nunca debería pasar en un set real terminado
    return "winner" if winner_games > loser_games else "loser"


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
        favorite_is_winner = m["winner_rank"] < m["loser_rank"]  # mejor ranking real
        hit = favorite_is_winner

        set1_winner = parse_set1_winner(m.get("score"))  # 'winner'|'loser'|None (del PARTIDO)
        set1_favorite_win = None
        if set1_winner is not None:
            # ¿el que ganó el set 1 es el mismo que el favorito por ranking?
            set1_favorite_win = (set1_winner == "winner") == favorite_is_winner

        cases.append({
            "tourney_name": m["tourney_name"], "level": m["level"], "date": m["date"],
            "round": m["round"], "favorite_rank": favorite_rank, "underdog_rank": underdog_rank,
            "hit": hit, "set1_favorite_win": set1_favorite_win,
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

    set1_cases = [c for c in cases if c["set1_favorite_win"] is not None]
    set1_hits = sum(1 for c in set1_cases if c["set1_favorite_win"])
    set1_n = len(set1_cases)

    return {
        "years": f"{start_year}-{end_year}", "total_matches_fetched": len(matches),
        "excluded_no_rank": excluded_no_rank, "evaluable_cases": n, "hits": hits,
        "overall_hit_rate": round(hits / n, 4) if n else None,
        "set1_evaluable_cases": set1_n, "set1_hits": set1_hits,
        "set1_favorite_win_rate": round(set1_hits / set1_n, 4) if set1_n else None,
        "by_level": by_level, "by_round": by_round,
        "caveat": (
            "SOLO ATP -- sin equivalente WTA del mismo autor. Ranking REAL "
            "al momento del partido, no aproximado. set1_favorite_win_rate: "
            "% de partidos donde el favorito por ranking se llevó el "
            "PRIMER SET (no necesariamente el partido)."
        ),
    }


if __name__ == "__main__":
    print(json.dumps(run_backtest(), indent=2, ensure_ascii=False))
