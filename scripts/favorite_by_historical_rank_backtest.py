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
            "rank_gap": underdog_rank - favorite_rank,
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

    # Condicional (2026-08-02, pedido explícito del usuario): de los
    # casos donde el FAVORITO se llevó el set 1, ¿en qué % también ganó
    # el partido? (distinto de `overall_hit_rate`, que promedia sobre
    # TODOS los partidos sin condicionar en el resultado del set 1).
    set1_won_by_favorite = [c for c in set1_cases if c["set1_favorite_win"]]
    match_hits_after_set1 = sum(1 for c in set1_won_by_favorite if c["hit"])
    match_after_set1_n = len(set1_won_by_favorite)

    # Categorización adicional (2026-08-02, pedido explícito del
    # usuario): mismo condicional de arriba, pero SOLO para partidos
    # donde el favorito además supera al underdog por >= 20 puestos de
    # ranking real -- ¿la brecha de ranking suma poder predictivo por
    # encima de "ganó el set 1" solo?
    _MIN_RANK_GAP = 20
    big_gap_set1_won_by_favorite = [c for c in set1_won_by_favorite if c["rank_gap"] >= _MIN_RANK_GAP]
    big_gap_hits = sum(1 for c in big_gap_set1_won_by_favorite if c["hit"])
    big_gap_n = len(big_gap_set1_won_by_favorite)

    # Winrate del UNDERDOG cuando pierde el set 1 -- pedido explícito
    # del usuario 2026-08-02, para dimensionar el RIESGO real de apostar
    # al favorito apenas se lleva el set 1 (sizing de posición: cuanto
    # mayor la tasa de remontada del underdog, menor debería ser el
    # tamaño de la apuesta al favorito). Es el complemento exacto de
    # `match_win_rate_after_favorite_wins_set1` (favorito ganó el
    # partido | favorito ganó set1) -- acá se nombra explícitamente
    # desde la perspectiva del underdog para el diseño de estrategia.
    underdog_comeback_hits = match_after_set1_n - match_hits_after_set1
    underdog_comeback_rate = (
        round(underdog_comeback_hits / match_after_set1_n, 4) if match_after_set1_n else None
    )

    # Desglose por brecha de ranking (buckets), para un sizing más fino
    # que un único umbral de 20 -- ¿la tasa de remontada del underdog
    # cae monótonamente a medida que crece la brecha de ranking real?
    _RANK_GAP_BUCKETS = [(0, 20), (20, 50), (50, 100), (100, None)]
    comeback_by_rank_gap: dict[str, dict[str, Any]] = {}
    for low, high in _RANK_GAP_BUCKETS:
        label = f"{low}-{high}" if high is not None else f"{low}+"
        bucket = [
            c for c in set1_won_by_favorite
            if c["rank_gap"] >= low and (high is None or c["rank_gap"] < high)
        ]
        b_n = len(bucket)
        b_underdog_wins = sum(1 for c in bucket if not c["hit"])
        comeback_by_rank_gap[label] = {
            "n": b_n, "underdog_comeback_hits": b_underdog_wins,
            "underdog_comeback_rate": round(b_underdog_wins / b_n, 4) if b_n else None,
        }

    # Caso simétrico (2026-08-02, pedido explícito del usuario): cuando
    # el UNDERDOG se lleva el set 1, ¿cuántas veces remonta el FAVORITO
    # y termina ganando el partido igual? Distinto de la remontada del
    # underdog de arriba -- acá el favorito arranca perdiendo.
    set1_won_by_underdog = [c for c in set1_cases if not c["set1_favorite_win"]]
    favorite_comeback_hits = sum(1 for c in set1_won_by_underdog if c["hit"])
    favorite_comeback_n = len(set1_won_by_underdog)
    favorite_comeback_rate = (
        round(favorite_comeback_hits / favorite_comeback_n, 4) if favorite_comeback_n else None
    )

    favorite_comeback_by_rank_gap: dict[str, dict[str, Any]] = {}
    for low, high in _RANK_GAP_BUCKETS:
        label = f"{low}-{high}" if high is not None else f"{low}+"
        bucket = [
            c for c in set1_won_by_underdog
            if c["rank_gap"] >= low and (high is None or c["rank_gap"] < high)
        ]
        b_n = len(bucket)
        b_favorite_wins = sum(1 for c in bucket if c["hit"])
        favorite_comeback_by_rank_gap[label] = {
            "n": b_n, "favorite_comeback_hits": b_favorite_wins,
            "favorite_comeback_rate": round(b_favorite_wins / b_n, 4) if b_n else None,
        }

    return {
        "years": f"{start_year}-{end_year}", "total_matches_fetched": len(matches),
        "excluded_no_rank": excluded_no_rank, "evaluable_cases": n, "hits": hits,
        "overall_hit_rate": round(hits / n, 4) if n else None,
        "set1_evaluable_cases": set1_n, "set1_hits": set1_hits,
        "set1_favorite_win_rate": round(set1_hits / set1_n, 4) if set1_n else None,
        "match_win_rate_after_favorite_wins_set1_evaluable_cases": match_after_set1_n,
        "match_win_rate_after_favorite_wins_set1_hits": match_hits_after_set1,
        "match_win_rate_after_favorite_wins_set1": (
            round(match_hits_after_set1 / match_after_set1_n, 4) if match_after_set1_n else None
        ),
        "match_win_rate_big_rank_gap_and_won_set1_evaluable_cases": big_gap_n,
        "match_win_rate_big_rank_gap_and_won_set1_hits": big_gap_hits,
        "match_win_rate_big_rank_gap_and_won_set1": round(big_gap_hits / big_gap_n, 4) if big_gap_n else None,
        "min_rank_gap_used": _MIN_RANK_GAP,
        "underdog_comeback_rate_after_losing_set1_evaluable_cases": match_after_set1_n,
        "underdog_comeback_rate_after_losing_set1_hits": underdog_comeback_hits,
        "underdog_comeback_rate_after_losing_set1": underdog_comeback_rate,
        "underdog_comeback_rate_by_rank_gap": comeback_by_rank_gap,
        "favorite_comeback_rate_after_losing_set1_evaluable_cases": favorite_comeback_n,
        "favorite_comeback_rate_after_losing_set1_hits": favorite_comeback_hits,
        "favorite_comeback_rate_after_losing_set1": favorite_comeback_rate,
        "favorite_comeback_rate_by_rank_gap": favorite_comeback_by_rank_gap,
        "by_level": by_level, "by_round": by_round,
        "caveat": (
            "SOLO ATP -- sin equivalente WTA del mismo autor. Ranking REAL "
            "al momento del partido, no aproximado. set1_favorite_win_rate: "
            "% de partidos donde el favorito por ranking se llevó el "
            "PRIMER SET (no necesariamente el partido). "
            "underdog_comeback_rate_after_losing_set1: % de partidos donde "
            "el underdog, tras perder el set 1 contra el favorito, IGUAL "
            "ganó el partido -- riesgo real de apostar al favorito solo "
            "porque se llevó el set 1, para sizing de posición. "
            "favorite_comeback_rate_after_losing_set1: caso simétrico -- % "
            "de partidos donde el favorito, tras PERDER el set 1 contra el "
            "underdog, remonta y gana el partido igual. "
            "match_win_rate_after_favorite_wins_set1: CONDICIONAL -- de los "
            "partidos donde el favorito se llevó el set 1, % en que además "
            "ganó el partido completo. "
            "match_win_rate_big_rank_gap_and_won_set1: mismo condicional, "
            f"restringido a partidos con brecha de ranking real >= "
            f"{_MIN_RANK_GAP} puestos."
        ),
    }


if __name__ == "__main__":
    print(json.dumps(run_backtest(), indent=2, ensure_ascii=False))
