"""Tests de `workers/tennis_set_tick_worker.py` -- 2026-09-01, resolución
del evento activo `KXATPSETWINNER`/`KXWTASETWINNER` para un `Match` ya
trackeado, y persistencia del tick real de precio. No pega red real:
`KalshiAPI` se monkeypatchea."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from database.db import init_db_historical
from database.historical.tennis.models import (
    LivePointSnapshot, Match, ProbabilityTick, SetProbabilityTick,
)
from workers import tennis_set_tick_worker as worker


@pytest.fixture(autouse=True)
def clean_tables():
    init_db_historical()
    models = (SetProbabilityTick, LivePointSnapshot, ProbabilityTick, Match)
    for model in models:
        model.delete().execute()
    yield
    for model in models:
        model.delete().execute()


def _match(market_id="KX-SET-WORKER", *, tour="ATP", player_a="Valdez", player_b="Montero"):
    return Match.create(
        market_id=market_id, ticker_a="A", ticker_b="B", tour=tour,
        surface="Hard", player_a_name=player_a, player_b_name=player_b,
        best_of=3, close_time="2026-09-05T18:00:00Z",
        detected_at=datetime.now(timezone.utc), winner=None,
    )


def _point_snapshot(match, current_set, minute=0):
    return LivePointSnapshot.create(
        match=match, flashscore_match_id="fs-x", player_a_is_flashscore_p1=True,
        observed_at=datetime.now(timezone.utc) + timedelta(minutes=minute),
        current_set=current_set, raw_point_json="{}",
    )


def test_current_set_reads_latest_flashscore_snapshot():
    match = _match()
    _point_snapshot(match, 1, minute=0)
    _point_snapshot(match, 2, minute=5)
    assert worker._current_set(match) == 2


def test_current_set_fails_closed_without_any_snapshot():
    match = _match()
    assert worker._current_set(match) is None


def test_resolve_active_set_market_matches_by_tour_names_and_set_number():
    match = _match(tour="ATP", player_a="Valdez", player_b="Montero")
    set_markets = [
        {
            "id": "KXATPSETWINNER-26SEP01VALMON-2", "tour": "ATP", "set_number": 2,
            "player_a_name": "Valdez", "player_b_name": "Montero",
            "current_a_price": 0.7, "current_b_price": 0.3,
        },
    ]
    result = worker._resolve_active_set_market(match, 2, set_markets)
    assert result["id"] == "KXATPSETWINNER-26SEP01VALMON-2"
    assert result["current_a_price"] == 0.7
    assert result["current_b_price"] == 0.3


def test_resolve_active_set_market_reorients_prices_when_players_swapped():
    """El evento real de Kalshi puede listar a los jugadores en el orden
    contrario al de `Match.player_a_name`/`player_b_name` -- los precios
    devueltos deben reorientarse, no asumir el mismo orden."""
    match = _match(tour="ATP", player_a="Valdez", player_b="Montero")
    set_markets = [
        {
            "id": "KXATPSETWINNER-26SEP01MONVAL-2", "tour": "ATP", "set_number": 2,
            "player_a_name": "Montero", "player_b_name": "Valdez",
            "current_a_price": 0.3, "current_b_price": 0.7,
        },
    ]
    result = worker._resolve_active_set_market(match, 2, set_markets)
    assert result["current_a_price"] == 0.7  # precio real de Valdez (player_a del Match)
    assert result["current_b_price"] == 0.3


def test_resolve_active_set_market_fails_closed_on_set_number_mismatch():
    match = _match(tour="ATP", player_a="Valdez", player_b="Montero")
    set_markets = [
        {
            "id": "KXATPSETWINNER-26SEP01VALMON-1", "tour": "ATP", "set_number": 1,
            "player_a_name": "Valdez", "player_b_name": "Montero",
            "current_a_price": 0.7, "current_b_price": 0.3,
        },
    ]
    assert worker._resolve_active_set_market(match, 2, set_markets) is None


def test_resolve_active_set_market_fails_closed_on_name_mismatch():
    match = _match(tour="ATP", player_a="Valdez", player_b="Montero")
    set_markets = [
        {
            "id": "KXATPSETWINNER-26SEP01ABCXYZ-2", "tour": "ATP", "set_number": 2,
            "player_a_name": "Someone Else", "player_b_name": "Another One",
            "current_a_price": 0.7, "current_b_price": 0.3,
        },
    ]
    assert worker._resolve_active_set_market(match, 2, set_markets) is None


class _FakeKalshiAPI:
    def __init__(self, markets_by_series):
        self._markets_by_series = markets_by_series

    def initialize(self):
        pass

    def get_markets(self, series_ticker=None, **kwargs):
        return self._markets_by_series.get(series_ticker, [])


def _raw_leg(event_ticker, ticker, sub_title, price):
    return {
        "id": ticker, "ticker": ticker, "current_a_price": price,
        "current_b_price": round(1 - price, 4), "close_time": "2026-09-05T18:00:00Z",
        "raw": {
            "event_ticker": event_ticker, "ticker": ticker,
            "title": f"Will {sub_title} win set 2 in Valdez vs Montero match?",
            "yes_sub_title": sub_title, "rules_secondary": "Valencia Open Semifinal",
        },
    }


def test_run_once_persists_a_real_set_tick_end_to_end(monkeypatch):
    match = _match(market_id="KX-E2E", tour="ATP", player_a="Valdez", player_b="Montero")
    _point_snapshot(match, 2)

    fake_markets = {
        "KXATPSETWINNER": [
            _raw_leg("KXATPSETWINNER-26SEP01VALMON-2", "KXATPSETWINNER-26SEP01VALMON-2-VAL", "Valdez", 0.62),
            _raw_leg("KXATPSETWINNER-26SEP01VALMON-2", "KXATPSETWINNER-26SEP01VALMON-2-MON", "Montero", 0.41),
        ],
    }
    monkeypatch.setattr(worker, "KalshiAPI", lambda: _FakeKalshiAPI(fake_markets))

    result = worker.run_once()
    assert result["ticked"] == 1
    assert result["errors"] == 0

    tick = SetProbabilityTick.get(SetProbabilityTick.match == match)
    assert tick.set_number == 2
    assert tick.market_price_a == 0.62
    assert tick.market_price_b == 0.41
    assert tick.event_ticker == "KXATPSETWINNER-26SEP01VALMON-2"


def test_run_once_reports_no_matching_event_when_series_has_no_active_set(monkeypatch):
    match = _match(market_id="KX-E2E-2", tour="ATP")
    _point_snapshot(match, 2)
    monkeypatch.setattr(worker, "KalshiAPI", lambda: _FakeKalshiAPI({}))

    result = worker.run_once()
    assert result["no_matching_event"] == 1
    assert result["ticked"] == 0
    assert SetProbabilityTick.select().count() == 0
