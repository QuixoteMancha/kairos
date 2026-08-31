"""Tests de `scripts/tennis/favourite_confirmation_backtest.py` --
2026-08-30, especificación cerrada de Favorito con confirmación de set 1."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from database.db import init_db_historical
from database.historical.tennis.models import (
    LivePointSnapshot, LiveStatsSnapshot, Match, MatchHistory, Player, ProbabilityTick,
)
from scripts.tennis.favourite_confirmation_backtest import (
    MIN_SPW_EDGE_PCT, WEAK_SECOND_SERVE_PCT, collect_favourite_confirmation_events,
)


@pytest.fixture(autouse=True)
def clean_tables():
    init_db_historical()
    models = (LiveStatsSnapshot, LivePointSnapshot, ProbabilityTick, Match, MatchHistory, Player)
    for model in models:
        model.delete().execute()
    yield
    for model in models:
        model.delete().execute()


def _player(name):
    return Player.create(canonical_name=name, created_at=datetime.now(timezone.utc))


def _match(market_id, *, history=None, tour="ATP", surface="Hard", best_of=3):
    return Match.create(
        market_id=market_id, ticker_a="A", ticker_b="B", tour=tour, surface=surface,
        player_a_name="Player A", player_b_name="Player B",
        best_of=best_of, close_time="2026-08-17T18:00:00Z",
        detected_at=datetime.now(timezone.utc), flashscore_match_id="fs-x",
        player_a_is_flashscore_p1=False, match_history=history,
    )


def _snapshot(match, minute, price, *, price_b=None, points_a=0, points_b=0, breaks_a=0, breaks_b=0,
              first_pct_a=60.0, first_pct_b=60.0,
              first_won_a=75.0, first_won_b=55.0,
              second_won_a=55.0, second_won_b=50.0,
              current_set=1):
    tick = ProbabilityTick.create(
        match=match, observed_at=datetime.now(timezone.utc) + timedelta(minutes=minute),
        market_price_a=price, market_price_b=price_b if price_b is not None else round(1 - price, 4),
    )
    LivePointSnapshot.create(
        match=match, probability_tick=tick, flashscore_match_id="fs-x",
        player_a_is_flashscore_p1=False,
        observed_at=datetime.now(timezone.utc) + timedelta(minutes=minute),
        raw_point_json="{}", current_set=current_set,
    )
    return LiveStatsSnapshot.create(
        match=match, probability_tick=tick, flashscore_match_id="fs-x",
        player_a_is_flashscore_p1=False,
        observed_at=datetime.now(timezone.utc) + timedelta(minutes=minute),
        raw_stats_json="{}",
        total_points_won_a=points_a, total_points_won_b=points_b,
        break_points_won_a=breaks_a, break_points_won_b=breaks_b,
        first_serve_pct_a=first_pct_a, first_serve_pct_b=first_pct_b,
        first_serve_points_won_pct_a=first_won_a, first_serve_points_won_pct_b=first_won_b,
        second_serve_points_won_pct_a=second_won_a, second_serve_points_won_pct_b=second_won_b,
    )


def _fake_favourite(a_is_fav=True):
    """Mockea `_is_favourite` para no depender de datos reales de forma/
    superficie -- ya testeado por separado el reuso de BetOnFavourite,
    acá se testea la lógica de confirmación post-set-1."""
    def _fn(history, tour, surface, side):
        return (side == "a") == a_is_fav
    return _fn


def test_holds_bet_when_all_confirmations_pass():
    a = _player("Fav A")
    b = _player("Dog B")
    history = MatchHistory.create(
        match_id="te:1", source="tennisexplorer", tour="ATP", match_date="2026-08-17",
        player_a=a, player_b=b, fetched_at=datetime.now(timezone.utc),
        score="6-3, 6-4", winner=a,
    )
    match = _match("KX-FC1", history=history)
    match.winner = "a"
    match.save()

    # set 1: A gana 6-3, con SPW edge >=8% y 2do servicio >40%.
    _snapshot(match, 0, 0.60, current_set=1,
              first_pct_a=65, first_won_a=80, second_won_a=60,
              first_pct_b=55, first_won_b=55, second_won_b=45)
    _snapshot(match, 5, 0.62, current_set=1,
              first_pct_a=65, first_won_a=80, second_won_a=60,
              first_pct_b=55, first_won_b=55, second_won_b=45)
    _snapshot(match, 10, 0.75, current_set=2,
              first_pct_a=65, first_won_a=80, second_won_a=60,
              first_pct_b=55, first_won_b=55, second_won_b=45)

    with patch("scripts.tennis.favourite_confirmation_backtest._is_favourite", _fake_favourite(True)):
        events = collect_favourite_confirmation_events()

    assert len(events) == 1
    ev = events[0]
    assert ev.side == "a"
    assert ev.withdrawn is False
    assert ev.closer_won_match is True
    assert ev.exit_price == 1.0


def test_withdraws_when_favourite_lost_set1():
    a = _player("Fav A2")
    b = _player("Dog B2")
    history = MatchHistory.create(
        match_id="te:2", source="tennisexplorer", tour="ATP", match_date="2026-08-17",
        player_a=a, player_b=b, fetched_at=datetime.now(timezone.utc),
        score="4-6, 6-3, 6-2", winner=a,  # a gana el PARTIDO igual, pero perdio el set 1
    )
    match = _match("KX-FC2", history=history)
    match.winner = "a"
    match.save()

    _snapshot(match, 0, 0.60, current_set=1,
              first_pct_a=65, first_won_a=80, second_won_a=60,
              first_pct_b=55, first_won_b=55, second_won_b=45)
    _snapshot(match, 5, 0.55, current_set=1,
              first_pct_a=65, first_won_a=80, second_won_a=60,
              first_pct_b=55, first_won_b=55, second_won_b=45)
    _snapshot(match, 10, 0.45, current_set=2,
              first_pct_a=65, first_won_a=80, second_won_a=60,
              first_pct_b=55, first_won_b=55, second_won_b=45)

    with patch("scripts.tennis.favourite_confirmation_backtest._is_favourite", _fake_favourite(True)):
        events = collect_favourite_confirmation_events()

    assert len(events) == 1
    ev = events[0]
    assert ev.withdrawn is True
    assert "perdio_set1" in ev.withdrawal_reason
    assert ev.exit_price == 0.45
    # se retiro con perdida (entro a 0.60, salio a 0.45) aunque el
    # favorito haya terminado ganando el partido real -- la decision de
    # retirar es correcta con la info disponible en ese momento.
    assert ev.match_return_pct == pytest.approx(100.0 * (0.45 - 0.60) / 0.60, abs=0.01)


def test_withdraws_when_spw_edge_weak_even_if_won_set1():
    a = _player("Fav A3")
    b = _player("Dog B3")
    history = MatchHistory.create(
        match_id="te:3", source="tennisexplorer", tour="ATP", match_date="2026-08-17",
        player_a=a, player_b=b, fetched_at=datetime.now(timezone.utc),
        score="7-6, 6-4", winner=a,
    )
    match = _match("KX-FC3", history=history)
    match.winner = "a"
    match.save()

    # A gano el set 1 (7-6) pero con SPW parejo (edge < 8%) -- se retira.
    _snapshot(match, 0, 0.60, current_set=1,
              first_pct_a=60, first_won_a=60, second_won_a=50,
              first_pct_b=60, first_won_b=58, second_won_b=48)
    _snapshot(match, 5, 0.62, current_set=1,
              first_pct_a=60, first_won_a=60, second_won_a=50,
              first_pct_b=60, first_won_b=58, second_won_b=48)
    _snapshot(match, 10, 0.65, current_set=2,
              first_pct_a=60, first_won_a=60, second_won_a=50,
              first_pct_b=60, first_won_b=58, second_won_b=48)

    with patch("scripts.tennis.favourite_confirmation_backtest._is_favourite", _fake_favourite(True)):
        events = collect_favourite_confirmation_events()

    assert len(events) == 1
    ev = events[0]
    assert ev.withdrawn is True
    assert "spw_edge_debil" in ev.withdrawal_reason


def test_withdraws_when_second_serve_weak_even_if_won_set1_and_spw_strong():
    a = _player("Fav A4")
    b = _player("Dog B4")
    history = MatchHistory.create(
        match_id="te:4", source="tennisexplorer", tour="ATP", match_date="2026-08-17",
        player_a=a, player_b=b, fetched_at=datetime.now(timezone.utc),
        score="6-2, 6-4", winner=a,
    )
    match = _match("KX-FC4", history=history)
    match.winner = "a"
    match.save()

    # A gano el set 1 con SPW edge fuerte, pero 2do servicio <=40%.
    _snapshot(match, 0, 0.60, current_set=1,
              first_pct_a=70, first_won_a=85, second_won_a=35,
              first_pct_b=50, first_won_b=50, second_won_b=40)
    _snapshot(match, 5, 0.65, current_set=1,
              first_pct_a=70, first_won_a=85, second_won_a=35,
              first_pct_b=50, first_won_b=50, second_won_b=40)
    _snapshot(match, 10, 0.72, current_set=2,
              first_pct_a=70, first_won_a=85, second_won_a=35,
              first_pct_b=50, first_won_b=50, second_won_b=40)

    with patch("scripts.tennis.favourite_confirmation_backtest._is_favourite", _fake_favourite(True)):
        events = collect_favourite_confirmation_events()

    assert len(events) == 1
    ev = events[0]
    assert ev.withdrawn is True
    assert "segundo_servicio_debil" in ev.withdrawal_reason
