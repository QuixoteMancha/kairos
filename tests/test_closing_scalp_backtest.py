"""Tests de `scripts/tennis/closing_scalp_backtest.py` -- 2026-08-28,
especificación cerrada de Closing Scalp tras contrastar contra el apunte
real de Obsidian."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import json

from datetime import date as _date

from database.db import init_db_historical
from database.historical.tennis.models import (
    LivePointSnapshot, LiveStatsSnapshot, Match, MatchDuration, MatchHistory,
    Player, ProbabilityTick, SetProbabilityTick, Tournament,
)
from scripts.tennis.closing_scalp_backtest import (
    MIN_SECOND_SERVE_PCT, MIN_SPW_EDGE_PCT, _favorite_age_and_fatigue,
    _set_serve_conditions,
    _spw, _spw_edge, _structural_advantage,
    collect_closing_scalp_events, run_closing_scalp_backtest, simulate_bankroll,
)


@pytest.fixture(autouse=True)
def clean_tables():
    init_db_historical()
    models = (
        LiveStatsSnapshot, LivePointSnapshot, ProbabilityTick, SetProbabilityTick,
        Match, MatchHistory, MatchDuration, Tournament, Player,
    )
    for model in models:
        model.delete().execute()
    yield
    for model in models:
        model.delete().execute()


def _player(name):
    return Player.create(canonical_name=name, created_at=datetime.now(timezone.utc))


def _match(market_id, *, history=None):
    return Match.create(
        market_id=market_id, ticker_a="A", ticker_b="B", tour="WTA",
        surface="Hard", player_a_name="Player A", player_b_name="Player B",
        best_of=3, close_time="2026-08-17T18:00:00Z",
        detected_at=datetime.now(timezone.utc), flashscore_match_id="fs-x",
        player_a_is_flashscore_p1=False, match_history=history,
    )


def _snapshot(match, minute, price, *, price_b=None, points_a=0, points_b=0, breaks_a=0, breaks_b=0,
              service_a=None, service_b=None, return_a=0, return_b=0,
              first_pct_a=60.0, first_pct_b=60.0,
              first_won_a=75.0, first_won_b=55.0,
              second_won_a=55.0, second_won_b=50.0,
              current_set=1, raw_point_json="{}"):
    tick = ProbabilityTick.create(
        match=match, observed_at=datetime.now(timezone.utc) + timedelta(minutes=minute),
        market_price_a=price, market_price_b=price_b if price_b is not None else round(1 - price, 4),
    )
    LivePointSnapshot.create(
        match=match, probability_tick=tick, flashscore_match_id="fs-x",
        player_a_is_flashscore_p1=False,
        observed_at=datetime.now(timezone.utc) + timedelta(minutes=minute),
        raw_point_json=raw_point_json, current_set=current_set,
    )
    # Existing pipeline fixtures model t0/t1/t2 as baseline, break, hold.
    # Keep that shorthand while allowing tests to state "no actual hold".
    if service_a is None:
        service_a = int(minute >= 10 and breaks_a > 0)
    if service_b is None:
        service_b = int(minute >= 10 and breaks_b > 0)
    return LiveStatsSnapshot.create(
        match=match, probability_tick=tick, flashscore_match_id="fs-x",
        player_a_is_flashscore_p1=False,
        observed_at=datetime.now(timezone.utc) + timedelta(minutes=minute),
        raw_stats_json=json.dumps({f"set_{current_set}": {
            "1st serve percentage": [f"{first_pct_a}%", f"{first_pct_b}%"],
            "1st serve points won": [f"{first_won_a}%", f"{first_won_b}%"],
            "2nd serve points won": [f"{second_won_a}%", f"{second_won_b}%"],
        }}),
        total_points_won_a=points_a, total_points_won_b=points_b,
        service_games_won_a=service_a, service_games_won_b=service_b,
        return_games_won_a=return_a, return_games_won_b=return_b,
        break_points_won_a=breaks_a, break_points_won_b=breaks_b,
        first_serve_pct_a=first_pct_a, first_serve_pct_b=first_pct_b,
        first_serve_points_won_pct_a=first_won_a, first_serve_points_won_pct_b=first_won_b,
        second_serve_points_won_pct_a=second_won_a, second_serve_points_won_pct_b=second_won_b,
    )


def _closing_point_raw(side="a", current_set=3, sequence="break_then_serve"):
    """Point feed immediately before ``side`` serves for the match."""
    candidate = 2 if side == "a" else 1
    opponent = 1 if candidate == 2 else 2
    sections = {}
    if current_set > 1:
        sections["set_1"] = {"games": [
            {"winner_side": candidate, "raw": {}} for _ in range(6)
        ] + [{"winner_side": opponent, "raw": {}} for _ in range(3)]}
    if sequence == "break_then_serve":
        hc, he = ((4, 5) if candidate == 2 else (5, 4))
        games = [{"winner_side": candidate,
                  "raw": {"HC": str(hc), "HE": str(he), "HG": str(opponent)}}]
    else:
        scores = ((3, 4), (3, 5), (4, 5)) if candidate == 2 else ((4, 3), (5, 3), (5, 4))
        games = [
            {"winner_side": candidate, "raw": {"HC": str(scores[0][0]), "HE": str(scores[0][1]), "HG": str(opponent)}},
            {"winner_side": candidate, "raw": {"HC": str(scores[1][0]), "HE": str(scores[1][1]), "HG": str(candidate)}},
            {"winner_side": opponent, "raw": {"HC": str(scores[2][0]), "HE": str(scores[2][1]), "HG": str(opponent)}},
        ]
    sections[f"set_{current_set}"] = {"games": games}
    return json.dumps(sections)


def test_spw_derivation_matches_manual_formula():
    match = _match("KX-SPW")
    snap = _snapshot(match, 0, 0.5, first_pct_a=60.0, first_won_a=80.0, second_won_a=40.0)
    # 0.6*80 + 0.4*40 = 48 + 16 = 64
    assert _spw(snap, "a") == pytest.approx(64.0)


def test_spw_edge_none_if_any_subfield_missing():
    match = _match("KX-SPW2")
    snap = _snapshot(match, 0, 0.5)
    snap.first_serve_pct_b = None
    snap.save()
    assert _spw_edge(snap, "a") is None


def test_set_conditions_do_not_inherit_strong_previous_sets():
    match = _match("KX-SET-SPW")
    snap = _snapshot(
        match, 10, 0.8, current_set=3,
        # Match-cumulative typed values remain strong.
        first_pct_a=65, first_won_a=80, second_won_a=60,
        first_pct_b=55, first_won_b=55, second_won_b=45,
    )
    snap.raw_stats_json = json.dumps({"set_3": {
        # But the decisive set itself is weak and must fail the gate.
        "1st serve percentage": ["60%", "60%"],
        "1st serve points won": ["60%", "59%"],
        "2nd serve points won": ["35%", "50%"],
    }})
    snap.save()

    assert _spw_edge(snap, "a") >= MIN_SPW_EDGE_PCT
    set_edge, set_second = _set_serve_conditions(snap, "a", 3)
    assert set_edge < MIN_SPW_EDGE_PCT
    assert set_second < MIN_SECOND_SERVE_PCT


def test_structural_advantage_requires_both_ranking_and_elo_favoring_side():
    a = _player("Fav A")
    b = _player("Dog B")
    history = MatchHistory.create(
        match_id="te:1", source="tennisexplorer", tour="WTA", match_date="2026-08-17",
        player_a=a, player_b=b, fetched_at=datetime.now(timezone.utc),
        player_a_ranking=10, player_b_ranking=50,
        player_a_surface_elo=1800, player_b_surface_elo=1600,
    )
    assert _structural_advantage(history, "a") is True
    assert _structural_advantage(history, "b") is False


def test_structural_advantage_none_when_surface_elo_missing_for_one_side():
    a = _player("Fav A2")
    b = _player("Dog B2")
    history = MatchHistory.create(
        match_id="te:2", source="tennisexplorer", tour="WTA", match_date="2026-08-17",
        player_a=a, player_b=b, fetched_at=datetime.now(timezone.utc),
        player_a_ranking=10, player_b_ranking=50,
        player_a_surface_elo=1800, player_b_surface_elo=None,
        player_a_elo=1700, player_b_elo=1500,
    )
    # cae a Elo general para AMBOS (no mezcla superficie de uno con general del otro)
    assert _structural_advantage(history, "a") is True


def test_simulate_bankroll_applies_1pct_stake_over_50usd_and_compounds_across_days():
    """2026-08-28, pedido explícito del usuario ("¿se está usando el 1%
    del bankroll?"): `run_closing_scalp_backtest` solo daba el retorno %
    promedio por trade aislado -- esto simula el bankroll real, mismo
    patrón que `bet_on_favourite_backtest.py::simulate()` (capital
    inicial $50, 1% de stake, agrupado por día)."""
    from scripts.tennis.closing_scalp_backtest import ClosingScalpEvent

    win = ClosingScalpEvent(
        match_id=1, side="a", entry_price=0.60,
        entry_observed_at=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
        spw_edge_at_entry=5.0, second_serve_pct_at_entry=50.0,
        current_set_at_entry=1, match_resolved=True, closer_won_match=True,
        exit_price=1.0, match_return_pct=round(100.0 * (1.0 - 0.60) / 0.60, 2),
    )
    loss = ClosingScalpEvent(
        match_id=2, side="a", entry_price=0.60,
        entry_observed_at=datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc),
        spw_edge_at_entry=5.0, second_serve_pct_at_entry=50.0,
        current_set_at_entry=1, match_resolved=True, closer_won_match=False,
        exit_price=0.0, match_return_pct=-100.0,
    )

    result = simulate_bankroll([win, loss], initial_capital=50.0, stake_pct=0.01)

    assert result["initial_capital"] == 50.0
    assert result["stake_pct"] == 0.01
    # 1% de $50 no alcanza para un contrato a $0.60. El simulador realista
    # omite ambas entradas en vez de fabricar contratos fraccionarios.
    assert result["n_trades"] == 0
    assert result["skipped_min_contract"] == 2
    assert result["final_capital"] == 50.0
    assert result["total_fees_pagados"] == 0.0
    assert result["max_drawdown_pct"] is not None


def test_simulate_bankroll_compounds_after_each_trade_same_day():
    """2026-08-29, pedido explícito del usuario ("el compounding se
    aplica tras cada trade") -- ANTES, dos trades el mismo día usaban el
    mismo capital de referencia para el 1% (solo componía entre días
    distintos). El 2do trade del mismo día debe calcular su stake sobre
    el capital YA actualizado por el 1er trade, no sobre el capital de
    arranque del día."""
    from outer_apis.kalshi.fees import order_fee
    from scripts.tennis.closing_scalp_backtest import ClosingScalpEvent

    same_day = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    win1 = ClosingScalpEvent(
        match_id=1, side="a", entry_price=0.60, entry_observed_at=same_day,
        spw_edge_at_entry=5.0, second_serve_pct_at_entry=50.0,
        current_set_at_entry=1, match_resolved=True, closer_won_match=True,
        exit_price=1.0, match_return_pct=round(100.0 * (1.0 - 0.60) / 0.60, 2),
    )
    win2 = ClosingScalpEvent(
        match_id=2, side="a", entry_price=0.60,
        entry_observed_at=same_day.replace(hour=13),  # mismo día, mas tarde
        spw_edge_at_entry=5.0, second_serve_pct_at_entry=50.0,
        current_set_at_entry=1, match_resolved=True, closer_won_match=True,
        exit_price=1.0, match_return_pct=round(100.0 * (1.0 - 0.60) / 0.60, 2),
    )

    result = simulate_bankroll([win1, win2], initial_capital=100.0, stake_pct=0.01)

    # trade 1: stake sobre $50. trade 2: stake sobre el capital YA
    # actualizado tras el trade 1 (más alto, porque ganó) -- si NO
    # componiera intra-día, el stake2 sería igual a stake1.
    fee1 = order_fee(0.60, contracts=1)
    capital_after_trade1 = 100.0 - 0.60 - fee1 + 1.0
    fee2 = order_fee(0.60, contracts=1)
    expected_capital = round(capital_after_trade1 - 0.60 - fee2 + 1.0, 2)

    assert result["n_trades"] == 2
    assert result["final_capital"] == pytest.approx(expected_capital, abs=0.01)


def test_bankroll_does_not_spend_unsettled_future_profit():
    from outer_apis.kalshi.fees import order_fee
    from scripts.tennis.closing_scalp_backtest import ClosingScalpEvent

    win = ClosingScalpEvent(
        match_id=1, side="a", entry_price=0.60,
        entry_observed_at=datetime(2026, 8, 20, 12, tzinfo=timezone.utc),
        settled_at=datetime(2026, 8, 20, 14, tzinfo=timezone.utc),
        spw_edge_at_entry=8.0, second_serve_pct_at_entry=50.0,
        current_set_at_entry=3, match_resolved=True, closer_won_match=True,
        exit_price=1.0, match_return_pct=66.67,
    )
    overlapping_loss = ClosingScalpEvent(
        match_id=2, side="a", entry_price=0.60,
        entry_observed_at=datetime(2026, 8, 20, 13, tzinfo=timezone.utc),
        settled_at=datetime(2026, 8, 20, 15, tzinfo=timezone.utc),
        spw_edge_at_entry=8.0, second_serve_pct_at_entry=50.0,
        current_set_at_entry=3, match_resolved=True, closer_won_match=False,
        exit_price=0.0, match_return_pct=-100.0,
    )

    result = simulate_bankroll([win, overlapping_loss], initial_capital=120.0, stake_pct=0.01)

    # At 12:00 two contracts fit. At 13:00 the win is still unsettled, so
    # only one contract fits. It cannot increase the second stake from 1 to 2.
    expected = 120.0 + (2 * 0.40 - order_fee(0.60, contracts=2)) \
        - (0.60 + order_fee(0.60, contracts=1))
    assert result["n_trades"] == 2
    assert result["final_capital"] == pytest.approx(expected, abs=0.01)


def test_serving_for_match_case_b_already_up_one_set_entering_set_two():
    """2026-08-29, pedido explícito del usuario ("tiene que ser cuando
    sirvan para el match, con TODOS los otros filtros") -- el caso (b)
    de `_is_match_closing_set`: el que cierra YA ganó el set 1 (calculable
    en vivo desde el punto-a-punto crudo de un set YA TERMINADO, no del
    resultado final), entra al set 2 con chance de cerrar 2-0."""
    a = _player("AlreadyUp1 A")
    b = _player("AlreadyUp1 B")
    history = MatchHistory.create(
        match_id="te:9", source="tennisexplorer", tour="WTA", match_date="2026-08-17",
        player_a=a, player_b=b, fetched_at=datetime.now(timezone.utc),
        player_a_ranking=5, player_b_ranking=80,
        player_a_surface_elo=1900, player_b_surface_elo=1500,
        score="6-3, 6-4", winner=a,
    )
    match = _match("KX-UP1", history=history)
    match.winner = "a"
    match.save()

    # set_1 YA terminado 6-3 a favor de A (player_a_is_flashscore_p1=False
    # en el helper -> winner_side=2 significa "gana A").
    raw = _closing_point_raw("a", 2, "break_hold_then_serve")

    _snapshot(match, 0, 0.55, points_a=10, points_b=10, breaks_a=0, breaks_b=0, current_set=2)
    _snapshot(match, 5, 0.60, points_a=14, points_b=11, breaks_a=1, breaks_b=0, current_set=2)
    _snapshot(match, 10, 0.65, points_a=18, points_b=13, breaks_a=1, breaks_b=0, current_set=2,
              raw_point_json=raw)

    events = collect_closing_scalp_events()
    assert len(events) == 1
    assert events[0].entry_sequence == "break_hold_then_serve"
    assert events[0].is_forced_final_set_at_entry is False  # NO es el set 3 forzoso
    assert events[0].current_set_at_entry == 2


def test_full_pipeline_finds_one_entry_and_computes_match_return_on_win():
    """2026-08-29: la entrada ahora SOLO se acepta en el set 3 (decisivo,
    forzosamente el último posible de un partido a mejor-de-3) -- filtro
    de entrada confirmado por el usuario, calculable en vivo."""
    a = _player("Closer A")
    b = _player("Rival B")
    history = MatchHistory.create(
        match_id="te:3", source="tennisexplorer", tour="WTA", match_date="2026-08-17",
        player_a=a, player_b=b, fetched_at=datetime.now(timezone.utc),
        player_a_ranking=5, player_b_ranking=80,
        player_a_surface_elo=1900, player_b_surface_elo=1500,
        score="4-6, 6-3, 6-4", winner=a,
    )
    match = _match("KX-FULL", history=history)
    match.winner = "a"
    match.save()

    # t0: baseline (ya en el set 3, decisivo). t1: A quiebra. t2: A
    # sostiene (hold confirmado) -- acá se evalúa la entrada.
    _snapshot(match, 0, 0.50, points_a=10, points_b=10, breaks_a=0, breaks_b=0, current_set=3)
    _snapshot(match, 5, 0.55, points_a=14, points_b=11, breaks_a=1, breaks_b=0, current_set=3,
              first_pct_a=65.0, first_won_a=80.0, second_won_a=60.0,
              first_pct_b=55.0, first_won_b=55.0, second_won_b=45.0)
    _snapshot(match, 10, 0.60, points_a=18, points_b=13, breaks_a=1, breaks_b=0, current_set=3,
              first_pct_a=65.0, first_won_a=80.0, second_won_a=60.0,
              first_pct_b=55.0, first_won_b=55.0, second_won_b=45.0,
              raw_point_json=_closing_point_raw("a", 3, "break_then_serve"))

    events = collect_closing_scalp_events()
    assert len(events) == 1
    ev = events[0]
    assert ev.side == "a"
    assert ev.entry_sequence == "break_then_serve"
    assert ev.entry_price == 0.60
    assert ev.match_resolved is True
    assert ev.closer_won_match is True
    assert ev.exit_price == 1.0
    assert ev.match_return_pct == pytest.approx(100.0 * (1.0 - 0.60) / 0.60, abs=0.01)
    assert ev.set_outcome_known is True
    assert ev.closer_won_set is True  # set 3 fue 6-4 a favor de A
    assert ev.is_forced_final_set_at_entry is True
    assert ev.is_final_set_of_match is True  # coherente con la versión retrospectiva

    report = run_closing_scalp_backtest()
    assert report["n_eventos_entrada_filtrados"] == 1
    assert report["nivel_partido__win_rate_pct"] == 100.0
    assert report["nivel_set__win_rate_pct"] == 100.0


def test_excludes_entry_when_spw_edge_below_threshold():
    a = _player("Even A")
    b = _player("Even B")
    history = MatchHistory.create(
        match_id="te:4", source="tennisexplorer", tour="WTA", match_date="2026-08-17",
        player_a=a, player_b=b, fetched_at=datetime.now(timezone.utc),
        player_a_ranking=5, player_b_ranking=80,
        player_a_surface_elo=1900, player_b_surface_elo=1500,
    )
    match = _match("KX-NOSPW", history=history)
    # first/second serve stats IGUALES para ambos -> SPW edge = 0, no
    # alcanza el umbral de 2 puntos porcentuales.
    _snapshot(match, 0, 0.50, points_a=10, points_b=10, breaks_a=0, breaks_b=0,
              first_pct_a=60, first_pct_b=60, first_won_a=70, first_won_b=70,
              second_won_a=50, second_won_b=50)
    _snapshot(match, 5, 0.55, points_a=14, points_b=11, breaks_a=1, breaks_b=0,
              first_pct_a=60, first_pct_b=60, first_won_a=70, first_won_b=70,
              second_won_a=50, second_won_b=50)
    _snapshot(match, 10, 0.60, points_a=18, points_b=13, breaks_a=1, breaks_b=0,
              first_pct_a=60, first_pct_b=60, first_won_a=70, first_won_b=70,
              second_won_a=50, second_won_b=50)

    assert collect_closing_scalp_events() == []


def test_excludes_entry_when_no_structural_advantage():
    a = _player("Underdog A")
    b = _player("Favorite B")
    history = MatchHistory.create(
        match_id="te:5", source="tennisexplorer", tour="WTA", match_date="2026-08-17",
        player_a=a, player_b=b, fetched_at=datetime.now(timezone.utc),
        player_a_ranking=80, player_b_ranking=5,  # A es el underdog en ranking
        player_a_surface_elo=1500, player_b_surface_elo=1900,
    )
    match = _match("KX-NOSTRUCT", history=history)
    _snapshot(match, 0, 0.50, points_a=10, points_b=10, breaks_a=0, breaks_b=0,
              first_pct_a=65, first_won_a=80, second_won_a=60,
              first_pct_b=55, first_won_b=55, second_won_b=45)
    _snapshot(match, 5, 0.55, points_a=14, points_b=11, breaks_a=1, breaks_b=0,
              first_pct_a=65, first_won_a=80, second_won_a=60,
              first_pct_b=55, first_won_b=55, second_won_b=45)
    _snapshot(match, 10, 0.60, points_a=18, points_b=13, breaks_a=1, breaks_b=0,
              first_pct_a=65, first_won_a=80, second_won_a=60,
              first_pct_b=55, first_won_b=55, second_won_b=45)

    # A domina las stats en vivo pero NO tiene ventaja estructural
    # pre-match -- debe excluirse igual (gate obligatorio, no opcional).
    assert collect_closing_scalp_events() == []


def test_entry_price_uses_market_price_b_when_closer_is_side_b():
    """2026-08-28, bug real encontrado en la primera corrida contra prod:
    el código usaba `market_price_a` SIEMPRE, sin importar qué lado
    cerraba -- retorno promedio inflado a ~1488% en el primer corte real.
    `market_price_a`/`market_price_b` son columnas SEPARADAS (no
    necesariamente `1-a`), por eso este test las fija a valores que NO
    son complementarios exactos, para que un regreso a `market_price_a`
    a secas se detecte."""
    a = _player("Rival A")
    b = _player("Closer B")
    history = MatchHistory.create(
        match_id="te:7", source="tennisexplorer", tour="WTA", match_date="2026-08-17",
        player_a=a, player_b=b, fetched_at=datetime.now(timezone.utc),
        player_a_ranking=80, player_b_ranking=5,
        player_a_surface_elo=1500, player_b_surface_elo=1900,
        score="6-3, 3-6, 4-6", winner=b,
    )
    match = _match("KX-SIDEB", history=history)
    match.winner = "b"
    match.save()

    # B domina (rompe y sostiene) en el set 3 (decisivo) -- market_price_a=0.85
    # (A caro, ganando poco probable) pero market_price_b=0.30 (deliberadamente
    # NO 0.15, para que el test falle si el código usa `1 - market_price_a`).
    _snapshot(match, 0, 0.85, price_b=0.30, points_a=10, points_b=10, breaks_a=0, breaks_b=0,
              first_pct_a=55, first_won_a=55, second_won_a=45,
              first_pct_b=65, first_won_b=80, second_won_b=60, current_set=3)
    _snapshot(match, 5, 0.80, price_b=0.35, points_a=11, points_b=14, breaks_a=0, breaks_b=1,
              first_pct_a=55, first_won_a=55, second_won_a=45,
              first_pct_b=65, first_won_b=80, second_won_b=60, current_set=3)
    _snapshot(match, 10, 0.75, price_b=0.40, points_a=13, points_b=18, breaks_a=0, breaks_b=1,
              first_pct_a=55, first_won_a=55, second_won_a=45,
              first_pct_b=65, first_won_b=80, second_won_b=60, current_set=3,
              raw_point_json=_closing_point_raw("b", 3, "break_then_serve"))

    events = collect_closing_scalp_events()
    assert len(events) == 1
    ev = events[0]
    assert ev.side == "b"
    assert ev.entry_price == 0.40  # market_price_b del snapshot de entrada, NO market_price_a
    assert ev.closer_won_match is True
    assert ev.match_return_pct == pytest.approx(100.0 * (1.0 - 0.40) / 0.40, abs=0.01)


def test_excludes_settled_endpoint_price_as_entry():
    a = _player("Settled A")
    b = _player("Settled B")
    history = MatchHistory.create(
        match_id="te:settled", source="tennisexplorer", tour="WTA",
        match_date="2026-08-17", player_a=a, player_b=b,
        fetched_at=datetime.now(timezone.utc), player_a_ranking=5,
        player_b_ranking=80, player_a_surface_elo=1900,
        player_b_surface_elo=1500,
    )
    match = _match("KX-SETTLED", history=history)
    _snapshot(match, 0, 0.90, breaks_a=0, current_set=3)
    _snapshot(match, 5, 0.95, breaks_a=1, current_set=3)
    _snapshot(match, 10, 1.0, breaks_a=1, service_a=1, current_set=3,
              first_pct_a=65, first_won_a=80, second_won_a=60,
              first_pct_b=55, first_won_b=55, second_won_b=45)

    assert collect_closing_scalp_events() == []


def test_second_serve_degradation_after_entry_no_longer_triggers_a_stop():
    """2026-08-28, pedido explícito del usuario: se elimina el stop-loss
    de 2do servicio -- queda SOLO como filtro de entrada. Aunque el 2do
    servicio del que cierra se derrumbe después de la entrada, la
    posición NO se corta -- se liquida contra el resultado real del
    partido (acá, pérdida total porque el que entró termina perdiendo)."""
    a = _player("Closer StopA")
    b = _player("Rival StopB")
    history = MatchHistory.create(
        match_id="te:6", source="tennisexplorer", tour="WTA", match_date="2026-08-17",
        player_a=a, player_b=b, fetched_at=datetime.now(timezone.utc),
        player_a_ranking=5, player_b_ranking=80,
        player_a_surface_elo=1900, player_b_surface_elo=1500,
        score="6-4, 4-6, 3-6", winner=b,
    )
    match = _match("KX-STOP", history=history)
    match.winner = "b"
    match.save()

    _snapshot(match, 0, 0.50, points_a=10, points_b=10, breaks_a=0, breaks_b=0, current_set=3,
              first_pct_a=65, first_won_a=80, second_won_a=60,
              first_pct_b=55, first_won_b=55, second_won_b=45)
    _snapshot(match, 5, 0.55, points_a=14, points_b=11, breaks_a=1, breaks_b=0, current_set=3,
              first_pct_a=65, first_won_a=80, second_won_a=60,
              first_pct_b=55, first_won_b=55, second_won_b=45)
    _snapshot(match, 10, 0.60, points_a=18, points_b=13, breaks_a=1, breaks_b=0, current_set=3,
              first_pct_a=65, first_won_a=80, second_won_a=60,
              first_pct_b=55, first_won_b=55, second_won_b=45,
              raw_point_json=_closing_point_raw("a", 3, "break_then_serve"))
    # el 2do servicio de A se derrumba después de la entrada -- ya no
    # importa para la salida, solo importaba al momento de entrar.
    _snapshot(match, 15, 0.40, points_a=19, points_b=20, breaks_a=1, breaks_b=1, current_set=3,
              first_pct_a=65, first_won_a=80, second_won_a=30,
              first_pct_b=55, first_won_b=55, second_won_b=45)

    events = collect_closing_scalp_events()
    assert len(events) == 1
    assert not hasattr(events[0], "second_serve_stop_triggered")
    assert events[0].closer_won_match is False
    assert events[0].exit_price == 0.0
    assert events[0].match_return_pct == -100.0


def test_is_final_set_of_match_true_when_entry_happens_in_the_deciding_set():
    """2026-08-29, hipótesis del usuario ("se vuelve decisivo cuando
    sacan para match") -- proxy: el set de la entrada es el ÚLTIMO
    jugado del partido (acá, el set 3 decisivo de un partido a 3 sets)."""
    a = _player("Decider A")
    b = _player("Decider B")
    history = MatchHistory.create(
        match_id="te:8", source="tennisexplorer", tour="WTA", match_date="2026-08-17",
        player_a=a, player_b=b, fetched_at=datetime.now(timezone.utc),
        player_a_ranking=5, player_b_ranking=80,
        player_a_surface_elo=1900, player_b_surface_elo=1500,
        score="4-6, 6-3, 6-2", winner=a,
    )
    match = _match("KX-DECIDER", history=history)
    match.winner = "a"
    match.save()

    _snapshot(match, 0, 0.50, points_a=10, points_b=10, breaks_a=0, breaks_b=0, current_set=3,
              first_pct_a=65, first_won_a=80, second_won_a=60,
              first_pct_b=55, first_won_b=55, second_won_b=45)
    _snapshot(match, 5, 0.55, points_a=14, points_b=11, breaks_a=1, breaks_b=0, current_set=3,
              first_pct_a=65, first_won_a=80, second_won_a=60,
              first_pct_b=55, first_won_b=55, second_won_b=45)
    _snapshot(match, 10, 0.70, points_a=18, points_b=13, breaks_a=1, breaks_b=0, current_set=3,
              first_pct_a=65, first_won_a=80, second_won_a=60,
              first_pct_b=55, first_won_b=55, second_won_b=45,
              raw_point_json=_closing_point_raw("a", 3, "break_then_serve"))

    events = collect_closing_scalp_events()
    assert len(events) == 1
    assert events[0].is_final_set_of_match is True


def test_serve_for_set_uses_real_set_market_price_not_match_price():
    """2026-09-01, pedido explícito del usuario: entrada en set 1 de un
    partido a mejor-de-3 (NO decisivo, ni caso (a) ni caso (b) de
    `_is_match_closing_set`) se clasifica `serve_for_set` y usa el
    precio REAL de `SetProbabilityTick` (mercado KXATPSETWINNER/
    KXWTASETWINNER), nunca el precio de partido (`ProbabilityTick`)."""
    a = _player("SetEntry A")
    b = _player("SetEntry B")
    history = MatchHistory.create(
        match_id="te:99", source="tennisexplorer", tour="WTA", match_date="2026-08-17",
        player_a=a, player_b=b, fetched_at=datetime.now(timezone.utc),
        player_a_ranking=5, player_b_ranking=80,
        player_a_surface_elo=1900, player_b_surface_elo=1500,
        score="6-3, 4-6, 6-2", winner=a,
    )
    match = _match("KX-SET1", history=history)
    match.winner = "a"
    match.save()

    _snapshot(match, 0, 0.50, points_a=10, points_b=10, breaks_a=0, breaks_b=0, current_set=1,
              first_pct_a=65, first_won_a=80, second_won_a=60,
              first_pct_b=55, first_won_b=55, second_won_b=45)
    _snapshot(match, 2, 0.52, points_a=14, points_b=11, breaks_a=1, breaks_b=0, current_set=1,
              first_pct_a=65, first_won_a=80, second_won_a=60,
              first_pct_b=55, first_won_b=55, second_won_b=45)
    tick = _snapshot(
        match, 5, 0.55, points_a=18, points_b=13, breaks_a=1, breaks_b=0, current_set=1,
        first_pct_a=65, first_won_a=80, second_won_a=60,
        first_pct_b=55, first_won_b=55, second_won_b=45,
        raw_point_json=_closing_point_raw("a", 1, "break_then_serve"),
    )
    entry_observed_at = tick.observed_at

    # Precio REAL del mercado de set (distinto del precio de partido de
    # arriba, 0.55) -- observado antes de la entrada.
    SetProbabilityTick.create(
        match=match, set_number=1, observed_at=entry_observed_at - timedelta(seconds=1),
        event_ticker="KXWTASETWINNER-26AUGXXABCD-1",
        market_price_a=0.80, market_price_b=0.20,
    )

    events = collect_closing_scalp_events()
    assert len(events) == 1
    ev = events[0]
    assert ev.entry_type == "serve_for_set"
    assert ev.entry_price == 0.80  # precio de SET, no el 0.55 de partido
    assert ev.is_forced_final_set_at_entry is False
    assert ev.closer_won_set is True  # set 1 fue 6-3 a favor de A
    assert ev.set_return_pct == pytest.approx(100.0 * (1.0 - 0.80) / 0.80, abs=0.01)
    # No es serve_for_match: no se completa el retorno de partido.
    assert ev.match_return_pct is None

    report = run_closing_scalp_backtest()
    assert report["n_eventos_serve_for_set"] == 1
    assert report["n_eventos_serve_for_match"] == 0
    assert report["serve_for_set__win_rate_pct"] == 100.0
    assert report["serve_for_set__retorno_promedio_pct"] == pytest.approx(
        100.0 * (1.0 - 0.80) / 0.80, abs=0.01,
    )

    # simulate_bankroll deliberadamente NO opera serve_for_set todavía
    # (ver docstring) -- no debe aparecer como trade.
    bankroll = simulate_bankroll(events)
    assert bankroll["n_trades"] == 0


def test_serve_for_set_fails_closed_without_a_set_probability_tick():
    """Sin ningún `SetProbabilityTick` todavía persistido para ese set,
    el evento se descarta -- nunca se cae al precio de partido ni se
    fabrica un precio."""
    a = _player("NoTick A")
    b = _player("NoTick B")
    history = MatchHistory.create(
        match_id="te:100", source="tennisexplorer", tour="WTA", match_date="2026-08-17",
        player_a=a, player_b=b, fetched_at=datetime.now(timezone.utc),
        player_a_ranking=5, player_b_ranking=80,
        player_a_surface_elo=1900, player_b_surface_elo=1500,
        score="6-3, 4-6, 6-2", winner=a,
    )
    match = _match("KX-SET1-NOTICK", history=history)
    match.winner = "a"
    match.save()

    _snapshot(match, 0, 0.50, points_a=10, points_b=10, breaks_a=0, breaks_b=0, current_set=1,
              first_pct_a=65, first_won_a=80, second_won_a=60,
              first_pct_b=55, first_won_b=55, second_won_b=45)
    _snapshot(match, 2, 0.52, points_a=14, points_b=11, breaks_a=1, breaks_b=0, current_set=1,
              first_pct_a=65, first_won_a=80, second_won_a=60,
              first_pct_b=55, first_won_b=55, second_won_b=45)
    _snapshot(
        match, 5, 0.55, points_a=18, points_b=13, breaks_a=1, breaks_b=0, current_set=1,
        first_pct_a=65, first_won_a=80, second_won_a=60,
        first_pct_b=55, first_won_b=55, second_won_b=45,
        raw_point_json=_closing_point_raw("a", 1, "break_then_serve"),
    )

    events = collect_closing_scalp_events()
    assert events == []


def test_age_fatigue_veto_blocks_an_otherwise_valid_entry():
    """2026-09-05, pedido explícito del usuario ("común para todas las
    estrategias... no se puede apostar sin importar la estrategia") --
    misma configuración que `test_full_pipeline_finds_one_entry_and_
    computes_match_return_on_win` (que SIN el veto genera 1 evento
    real), pero el favorito tiene 30+ años y >=270 minutos acumulados
    en el mismo torneo antes de este partido -- celda real del análisis
    histórico con WR<60%. El evento no debe generarse."""
    tournament = Tournament.create(
        year=2026, tournament_name="Veto Open", city="Veto City", tour="WTA",
        fetched_at=datetime.now(timezone.utc),
    )
    a = _player("Veteran Closer A")
    a.birthdate = _date(1994, 1, 1)  # 32+ años al 2026-08-17
    a.save()
    b = _player("Rival B")

    # 2 partidos previos REALES en el mismo torneo, duración conocida,
    # suman >=270 min -- exactamente lo que exige `age_fatigue_veto`.
    for i, minutes in enumerate((150, 130)):
        duration = MatchDuration.create(
            flashscore_match_id=f"veto-prior-{i}", total_minutes=minutes,
            fetched_at=datetime.now(timezone.utc),
        )
        MatchHistory.create(
            match_id=f"te:veto-prior-{i}", source="tennisexplorer", tour="WTA",
            match_date=f"2026-08-1{5 + i}", tournament=tournament,
            player_a=a, player_b=_player(f"Prior Rival {i}"),
            fetched_at=datetime.now(timezone.utc), duration=duration,
        )

    history = MatchHistory.create(
        match_id="te:veto-current", source="tennisexplorer", tour="WTA",
        match_date="2026-08-17", tournament=tournament,
        player_a=a, player_b=b, fetched_at=datetime.now(timezone.utc),
        player_a_ranking=5, player_b_ranking=80,
        player_a_surface_elo=1900, player_b_surface_elo=1500,
        score="6-3, 6-4", winner=a,
    )

    age, cum_minutes = _favorite_age_and_fatigue(history, "a")
    assert age is not None and age >= 30
    assert cum_minutes == 280

    match = _match("KX-VETO", history=history)
    match.winner = "a"
    match.save()

    _snapshot(match, 0, 0.55, points_a=10, points_b=6, breaks_a=1, breaks_b=0, current_set=3)
    _snapshot(match, 5, 0.60, points_a=14, points_b=8, breaks_a=1, breaks_b=0, current_set=3)
    _snapshot(match, 10, 0.65, points_a=18, points_b=10, breaks_a=1, breaks_b=0, current_set=3,
              raw_point_json=_closing_point_raw("a", 1, "break_then_serve"))

    events = collect_closing_scalp_events()
    assert events == []
