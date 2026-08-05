"""Tests del backtest filtrado por rank_gap>=20 + winrate por
superficie (2026-08-04, pedido explícito del usuario)."""

from __future__ import annotations

from unittest.mock import patch

from scripts.tennis.favorite_surface_winrate_filter_backtest import (
    _build_surface_winrate_tracker, run_filtered_backtest, _MIN_RANK_GAP, _MIN_SURFACE_MATCHES,
)


def _row(date, winner, winner_rank, loser, loser_rank, surface="Hard", best_of=3, score="6-4 6-3"):
    return {
        "tourney_name": "X", "level": "M", "date": date, "round": "F",
        "surface": surface, "best_of": best_of,
        "winner_name": winner, "winner_rank": winner_rank, "winner_age": 25.0,
        "loser_name": loser, "loser_rank": loser_rank, "loser_age": 25.0,
        "score": score, "minutes": 95,
    }


def test_surface_winrate_is_point_in_time_correct():
    """El winrate del favorito en un partido de evaluación NO debe
    incluir el resultado de partidos FUTUROS -- solo los previos."""
    matches = [
        _row("2010-01-01", winner="A", winner_rank=5, loser="B", loser_rank=200),  # warm-up: A gana en Hard
        _row("2010-02-01", winner="Z", winner_rank=300, loser="C", loser_rank=100),  # warm-up: C también tiene historial en Hard (fail-closed necesita ambos)
        _row("2016-01-01", winner="A", winner_rank=5, loser="C", loser_rank=100),  # eval: A favorito, ya tiene 1-0 en Hard
        _row("2016-06-01", winner="A", winner_rank=5, loser="D", loser_rank=100),  # eval, posterior -- NO debe afectar al caso de arriba
    ]
    with patch(
        "scripts.tennis.favorite_surface_winrate_filter_backtest.get_matches_range", return_value=matches,
    ):
        cases, meta = _build_surface_winrate_tracker()
    # el primer caso de eval (2016-01-01): A tiene 1 victoria previa (del warm-up) en Hard, 0 derrotas -> winrate 1.0
    case_2016_01 = next(c for c in cases if c["date"] == "2016-01-01")
    assert case_2016_01["favorite_surface_winrate"] == 1.0


def test_filters_by_rank_gap_and_surface_winrate():
    matches = [
        # warm-up: A (futuro favorito con gap>=20) construye buen historial en Hard
        _row("2010-01-01", winner="A", winner_rank=5, loser="Z", loser_rank=300, surface="Hard"),
        _row("2011-01-01", winner="A", winner_rank=5, loser="Z", loser_rank=300, surface="Hard"),
        # warm-up: B (underdog en el caso de eval) pierde seguido en Hard
        _row("2010-06-01", winner="Z", winner_rank=300, loser="B", loser_rank=100, surface="Hard"),
        # warm-up: C (underdog del 2do caso) también necesita historial real en Hard
        _row("2010-07-01", winner="Z", winner_rank=300, loser="C", loser_rank=15, surface="Hard"),
        # eval: A (rank 5) vs B (rank 100) -- gap=95 (>=20), A tiene mejor winrate en Hard -> CALIFICA
        _row("2016-01-01", winner="A", winner_rank=5, loser="B", loser_rank=100, surface="Hard"),
        # eval: A (rank 5) vs C (rank 15) -- gap=10 (<20) -- NO CALIFICA por gap
        _row("2016-02-01", winner="A", winner_rank=5, loser="C", loser_rank=15, surface="Hard"),
    ]
    with patch(
        "scripts.tennis.favorite_surface_winrate_filter_backtest.get_matches_range", return_value=matches,
    ):
        # min_surface_matches=0 -- este test aísla el filtro de rank_gap/winrate,
        # no el umbral de tamaño de muestra (ver test dedicado más abajo).
        result = run_filtered_backtest(initial_capital=50.0, max_stake_usd=None, min_surface_matches=0)
    assert result["filtered_cases_n"] == 1  # solo el partido A vs B califica
    assert result["total_cases_with_surface_history"] == 2  # A vs B y A vs C ambos tienen historial de superficie


def test_excludes_case_when_no_surface_history_for_either_player():
    matches = [
        # SIN warm-up -- ninguno de los 2 tiene historial previo en Hard
        _row("2016-01-01", winner="A", winner_rank=5, loser="B", loser_rank=100, surface="Hard"),
    ]
    with patch(
        "scripts.tennis.favorite_surface_winrate_filter_backtest.get_matches_range", return_value=matches,
    ):
        cases, meta = _build_surface_winrate_tracker()
    assert cases == []
    assert meta["skipped_no_surface_history"] == 1


def test_min_rank_gap_constant_is_20():
    assert _MIN_RANK_GAP == 20


def test_min_surface_matches_default_is_0():
    """2026-08-04, pedido explícito del usuario: probó min_surface_matches=20
    y luego pidió revertirlo ("no quita los 20 partidos de muestra") --
    default final = 0, vuelve al comportamiento original (solo exige
    >=1 partido previo real, fail-closed de por sí)."""
    assert _MIN_SURFACE_MATCHES == 0


def test_run_filtered_backtest_uses_default_min_surface_matches_of_0():
    matches = [
        # warm-up: A tiene SOLO 1 partido previo en Hard (winrate 1.0 con n=1)
        _row("2010-01-01", winner="A", winner_rank=5, loser="Z", loser_rank=300, surface="Hard"),
        # warm-up: B tiene SOLO 1 partido previo en Hard también (pierde, winrate 0.0)
        _row("2010-06-01", winner="Z", winner_rank=300, loser="B", loser_rank=100, surface="Hard"),
        # eval: A (rank 5) vs B (rank 100) -- gap>=20, A tiene mejor winrate (1.0 > 0.0), n=1 cada uno
        _row("2016-01-01", winner="A", winner_rank=5, loser="B", loser_rank=100, surface="Hard"),
    ]
    with patch(
        "scripts.tennis.favorite_surface_winrate_filter_backtest.get_matches_range", return_value=matches,
    ):
        result = run_filtered_backtest(initial_capital=50.0, max_stake_usd=None)  # sin pasar min_surface_matches -- usa el default 0
    assert result["min_surface_matches_used"] == 0
    assert result["filtered_cases_n"] == 1  # califica igual con n=1, default 0 = sin umbral extra


def test_min_surface_matches_excludes_thin_sample_case():
    """2026-08-04, pedido explícito -- exigir >=N partidos previos
    reales en la superficie (no solo >=1) para confiar en el winrate."""
    matches = [
        # warm-up: A tiene SOLO 1 partido previo en Hard (winrate 1.0 con n=1, muestra chica)
        _row("2010-01-01", winner="A", winner_rank=5, loser="Z", loser_rank=300, surface="Hard"),
        # warm-up: B tiene SOLO 1 partido previo en Hard también (pierde, winrate 0.0)
        _row("2010-06-01", winner="Z", winner_rank=300, loser="B", loser_rank=100, surface="Hard"),
        # eval: A (rank 5) vs B (rank 100) -- gap>=20, A tiene mejor winrate (1.0 > 0.0) pero con n=1 cada uno
        _row("2016-01-01", winner="A", winner_rank=5, loser="B", loser_rank=100, surface="Hard"),
    ]
    with patch(
        "scripts.tennis.favorite_surface_winrate_filter_backtest.get_matches_range", return_value=matches,
    ):
        no_threshold = run_filtered_backtest(initial_capital=50.0, max_stake_usd=None, min_surface_matches=0)
        with_threshold = run_filtered_backtest(initial_capital=50.0, max_stake_usd=None, min_surface_matches=100)
    assert no_threshold["filtered_cases_n"] == 1  # sin umbral, califica con n=1
    assert with_threshold["filtered_cases_n"] == 0  # con umbral 100, se excluye por muestra chica
    assert with_threshold["min_surface_matches_used"] == 100


def test_prioritizes_higher_ranked_favorite_within_daily_cap():
    """2026-08-04, pedido explícito del usuario: dentro del cupo del 50%
    de capital diario, si 2+ partidos del mismo día califican, el
    favorito de MEJOR ranking (número más bajo) debe procesarse primero
    -- se verifica ordenando `cases` por (date, favorite_rank) antes de
    llamar a `_simulate_realistic_all_strategies` (el cupo acepta en
    orden de llegada de la lista)."""
    matches = [
        # warm-up: A, B (mejor rankeado en eval) y Z construyen historial real en Hard
        _row("2010-01-01", winner="A", winner_rank=5, loser="Z", loser_rank=300, surface="Hard"),
        _row("2010-02-01", winner="B", winner_rank=2, loser="Z", loser_rank=300, surface="Hard"),
        _row("2010-03-01", winner="Z", winner_rank=300, loser="C", loser_rank=150, surface="Hard"),
        _row("2010-04-01", winner="Z", winner_rank=300, loser="D", loser_rank=150, surface="Hard"),
        # mismo día: A (rank 5) vs C (rank 150) y B (rank 2) vs D (rank 150) -- ambos califican (gap>=20)
        # A aparece PRIMERO en la lista de entrada, pero B tiene mejor ranking (2 < 5) -> debe priorizarse
        _row("2016-01-01", winner="A", winner_rank=5, loser="C", loser_rank=150, surface="Hard"),
        _row("2016-01-01", winner="B", winner_rank=2, loser="D", loser_rank=150, surface="Hard"),
    ]
    with patch(
        "scripts.tennis.favorite_surface_winrate_filter_backtest.get_matches_range", return_value=matches,
    ):
        cases, meta = _build_surface_winrate_tracker()
    day_cases = [c for c in cases if c["date"] == "2016-01-01"]
    assert len(day_cases) == 2
    # orden de entrada real (antes de priorizar): A (rank 5) va primero
    assert day_cases[0]["favorite_rank"] == 5
    # tras ordenar por (date, favorite_rank) -- lo que hace run_filtered_backtest --
    # B (rank 2) debe quedar primero en la cola de aceptación del cupo diario
    prioritized = sorted(day_cases, key=lambda c: (c["date"], c["favorite_rank"]))
    assert prioritized[0]["favorite_rank"] == 2
    assert prioritized[1]["favorite_rank"] == 5
