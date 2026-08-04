"""Backtest ATP filtrado: solo se apuesta al favorito cuando (1) supera
al underdog por >=20 puestos de ranking Y (2) tiene mejor winrate REAL
por superficie que el underdog -- 2026-08-04, pedido explícito del
usuario ("añadamos que sólo se apuesta al favorito que supera en 20
puntos o más al underdog y que supera en winrate por superficie al
underdog").

WINRATE POR SUPERFICIE: auto-calculado desde el propio TML-Database
(mismo criterio point-in-time que `self_computed_elo_backtest.py`), NO
desde Tennis Abstract (esa fuente es snapshot ACTUAL, lookahead real si
se aplicara a partidos históricos, mismo problema ya documentado). Solo
cuenta partidos REALES en esa superficie específica anteriores a la
fecha del partido evaluado -- un jugador sin historial previo en esa
superficie queda fail-closed (excluido, no se le asume 0% ni 50%).

WARM-UP 1968-2015 (ampliado 2026-08-04, pedido explícito del usuario
-- "podemos ampliar el historial antes de los 2000's"): TML-Database
cubre real desde 1968, verificado que `surface`/`winner_name`/
`loser_name` (los únicos campos que necesita el tracker de superficie,
a diferencia del Elo que sí necesita ranking) están presentes al 100%
incluso en 1968-1975. El winrate por superficie usa TODO ese rango como
warm-up, no solo 2000-2015 -- más historial real = winrate por
superficie más preciso para jugadores con carreras largas, y sigue
siendo point-in-time correcto por construcción (nunca usa un partido
futuro)."""

from __future__ import annotations

import json
from typing import Any

from outer_apis.scrapers.tennis.tml_database_scraper import get_matches_range, TOUR_LEVELS_250_PLUS
from scripts.favorite_by_historical_rank_backtest import (
    parse_set1_winner, parse_set2_winner, _set1_game_diff, _simulate_realistic_all_strategies,
)

_WARMUP_START_YEAR = 1968
_EVAL_START_YEAR = 2016
_DEFAULT_END_YEAR = 2026
_MIN_RANK_GAP = 20


def _build_surface_winrate_tracker() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Recorre TODO TML-Database (warm-up + evaluación) UNA sola vez en
    orden cronológico, manteniendo win/loss REAL por (jugador,
    superficie) -- y arma `cases` con los 2 winrates point-in-time (el
    del favorito y el del underdog, ANTES de este partido) además de
    todos los campos que ya usa `_simulate_realistic_all_strategies`."""
    matches = get_matches_range(_WARMUP_START_YEAR, _DEFAULT_END_YEAR, levels=TOUR_LEVELS_250_PLUS)
    matches.sort(key=lambda m: m["date"])

    # (player, surface) -> [wins, losses]
    record: dict[tuple[str, str], list[int]] = {}
    eval_cutoff = f"{_EVAL_START_YEAR}-01-01"

    cases: list[dict[str, Any]] = []
    skipped_no_rank = skipped_no_surface_history = 0

    for m in matches:
        winner_name, loser_name, surface = m["winner_name"], m["loser_name"], m.get("surface")

        if m["date"] >= eval_cutoff and m["winner_rank"] is not None and m["loser_rank"] is not None and surface:
            favorite_is_winner = m["winner_rank"] < m["loser_rank"]
            favorite_name = winner_name if favorite_is_winner else loser_name
            underdog_name = loser_name if favorite_is_winner else winner_name
            favorite_rank = min(m["winner_rank"], m["loser_rank"])
            underdog_rank = max(m["winner_rank"], m["loser_rank"])

            fav_rec = record.get((favorite_name, surface))
            dog_rec = record.get((underdog_name, surface))
            if fav_rec is None or dog_rec is None or (fav_rec[0] + fav_rec[1]) == 0 or (dog_rec[0] + dog_rec[1]) == 0:
                skipped_no_surface_history += 1
            else:
                fav_wr = fav_rec[0] / (fav_rec[0] + fav_rec[1])
                dog_wr = dog_rec[0] / (dog_rec[0] + dog_rec[1])

                set1_winner = parse_set1_winner(m.get("score"))
                set1_favorite_win = None if set1_winner is None else (set1_winner == "winner") == favorite_is_winner
                set2_winner = parse_set2_winner(m.get("score"))
                set2_favorite_win = None if set2_winner is None else (set2_winner == "winner") == favorite_is_winner

                cases.append({
                    "date": m["date"], "favorite_rank": favorite_rank, "underdog_rank": underdog_rank,
                    "rank_gap": underdog_rank - favorite_rank, "hit": favorite_is_winner,
                    "set1_favorite_win": set1_favorite_win, "set2_favorite_win": set2_favorite_win,
                    "surface": surface, "best_of": m.get("best_of"),
                    "favorite_age": m.get("winner_age") if favorite_is_winner else m.get("loser_age"),
                    "set1_diff": _set1_game_diff(m.get("score")),
                    "favorite_surface_winrate": round(fav_wr, 4),
                    "underdog_surface_winrate": round(dog_wr, 4),
                    # 2026-08-04, pedido explícito -- muestra usada para el
                    # winrate de arriba, para poder filtrar por un mínimo
                    # real de partidos previos en esa superficie (evitar
                    # confiar en un winrate calculado sobre n chico, ej.
                    # 1/1 = 100%).
                    "favorite_surface_matches": fav_rec[0] + fav_rec[1],
                    "underdog_surface_matches": dog_rec[0] + dog_rec[1],
                })
        elif m["date"] >= eval_cutoff:
            skipped_no_rank += 1

        # actualizar el tracker DESPUÉS de usarlo -- point-in-time correcto.
        if surface:
            wr = record.setdefault((winner_name, surface), [0, 0])
            wr[0] += 1
            lr = record.setdefault((loser_name, surface), [0, 0])
            lr[1] += 1

    return cases, {"skipped_no_rank_or_surface": skipped_no_rank, "skipped_no_surface_history": skipped_no_surface_history}


_MIN_SURFACE_MATCHES = 20


def run_filtered_backtest(
    initial_capital: float = 50.0,
    max_stake_usd: float | None = 60_000.0,
    min_surface_matches: int = _MIN_SURFACE_MATCHES,
) -> dict[str, Any]:
    """`min_surface_matches`: 2026-08-04, pedido explícito -- exige que
    TANTO el favorito COMO el underdog tengan al menos ese número de
    partidos previos reales (point-in-time) en la superficie del
    partido antes de confiar en su winrate por superficie (evita
    aceptar un caso con, por ej., 1/1 = 100% winrate). Default = 20,
    decisión explícita del usuario tras evaluar 0 (solo exige >=1,
    ruidoso), 100 (muestra sólida pero colapsa la población a 2,222
    casos y el capital final compuesto queda irrelevante) -- 20 es el
    punto intermedio elegido."""
    all_cases, meta = _build_surface_winrate_tracker()

    filtered_cases = [
        c for c in all_cases
        if c["rank_gap"] >= _MIN_RANK_GAP
        and c["favorite_surface_winrate"] > c["underdog_surface_winrate"]
        and c["favorite_surface_matches"] >= min_surface_matches
        and c["underdog_surface_matches"] >= min_surface_matches
    ]

    # 2026-08-04, pedido explícito del usuario: "dar prioridad a los de
    # ranking más alto, manteniendo la regla del 50% de capital máximo
    # ocupado diario" -- el cupo de concurrencia de
    # `_simulate_realistic_all_strategies` acepta los partidos de un
    # mismo día EN EL ORDEN en que llegan hasta llenar el 50%, después
    # descarta el resto. Ordenar los casos por (fecha, favorite_rank
    # ascendente) ANTES de simular logra la priorización pedida sin
    # tocar la lógica del cupo -- dentro de cada día, los favoritos
    # mejor rankeados (número de ranking más bajo) quedan primeros en la
    # cola y se quedan con el cupo antes que los de ranking más bajo.
    filtered_cases_prioritized = sorted(filtered_cases, key=lambda c: (c["date"], c["favorite_rank"]))
    all_cases_prioritized = sorted(all_cases, key=lambda c: (c["date"], c["favorite_rank"]))

    baseline_result = _simulate_realistic_all_strategies(
        all_cases_prioritized, initial_capital=initial_capital, max_stake_usd=max_stake_usd,
        stake_pct=0.01, max_simultaneous_pct=0.5,
    )
    filtered_result = _simulate_realistic_all_strategies(
        filtered_cases_prioritized, initial_capital=initial_capital, max_stake_usd=max_stake_usd,
        stake_pct=0.01, max_simultaneous_pct=0.5,
    )

    baseline_hits = sum(1 for c in all_cases if c["hit"])
    filtered_hits = sum(1 for c in filtered_cases if c["hit"])

    return {
        "min_rank_gap_used": _MIN_RANK_GAP,
        "min_surface_matches_used": min_surface_matches,
        "total_cases_with_surface_history": len(all_cases), **meta,
        "filtered_cases_n": len(filtered_cases),
        "baseline_hit_rate": round(baseline_hits / len(all_cases), 4) if all_cases else None,
        "filtered_hit_rate": round(filtered_hits / len(filtered_cases), 4) if filtered_cases else None,
        "baseline_strategies": baseline_result["strategies"],
        "filtered_strategies": filtered_result["strategies"],
        "caveat": (
            "Winrate por superficie AUTO-CALCULADO desde TML-Database "
            "(warm-up 1968-2015, point-in-time correcto), no Tennis "
            "Abstract (esa fuente es snapshot actual, lookahead real si "
            "se aplicara así). Jugador sin historial previo real en esa "
            "superficie puntual -> excluido (fail-closed, nunca se "
            "asume 50%/0%)."
        ),
    }


if __name__ == "__main__":
    import sys

    min_matches = int(sys.argv[1]) if len(sys.argv) > 1 else _MIN_SURFACE_MATCHES
    result = run_filtered_backtest(min_surface_matches=min_matches)
    print(json.dumps(
        {k: v for k, v in result.items() if k not in ("baseline_strategies", "filtered_strategies")},
        indent=2, ensure_ascii=False,
    ))
    print("=== BASELINE (todos los casos) ===")
    print(json.dumps(result["baseline_strategies"], indent=2, ensure_ascii=False))
    print(f"=== FILTRADO (gap>=20 + mejor winrate superficie + min_surface_matches={min_matches}) ===")
    print(json.dumps(result["filtered_strategies"], indent=2, ensure_ascii=False))
