"""Tests de `scripts/backfill_sf_final_matches.py` (2026-08-02, pedido
explícito del usuario: persistir en DB los partidos SF/Final escaneados,
con score, clima real, e historial de probabilidad real de Kalshi)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from database.db import init_db_historical, db_historical
from database.historical.match_models import Match as TennisScannedMatch
from database.historical.match_models import ProbabilityTick as TennisMatchProbabilityTick
from database.historical.match_models import MatchStats as TennisMatchStats
from scripts.tennis.backfill_sf_final_matches import backfill_match, _parse_trades, _find_real_score


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        TennisMatchStats.delete().execute()
        TennisMatchProbabilityTick.delete().execute()
        TennisScannedMatch.delete().execute()
    yield
    with db_historical.connection_context():
        TennisMatchStats.delete().execute()
        TennisMatchProbabilityTick.delete().execute()
        TennisScannedMatch.delete().execute()


def _entry(**overrides):
    base = {
        "event_ticker": "EVT1", "series": "KXATPMATCH", "round": "Final",
        "tournament": "Washington", "match_date": "2026-08-01",
        "player_a": "Player A", "player_b": "Player B", "winner": "Player A",
        "ticker_a": "EVT1-A", "ticker_b": "EVT1-B",
        "flashscore_match_id": "fs123", "flashscore_stats": {"match": {"Aces": ["3", "1"]}},
    }
    base.update(overrides)
    return base


def _fake_trades(prices_with_offsets):
    return [
        {"created_time": f"2026-08-01T{10+i}:00:00Z", "yes_price_dollars": str(p)}
        for i, p in enumerate(prices_with_offsets)
    ]


def test_skips_registration_without_real_trades():
    api = MagicMock()
    api.get_trades.return_value = []
    result = backfill_match(api, _entry())
    assert "skipped" in result["steps"]["registration"]
    assert TennisScannedMatch.select().count() == 0


def test_creates_match_with_correct_winner_side():
    api = MagicMock()
    api.get_trades.return_value = _fake_trades([0.3, 0.5, 0.9])
    with patch("scripts.tennis.backfill_sf_final_matches.populate_weather_for_match", return_value={"skipped": True}), \
         patch("scripts.tennis.backfill_sf_final_matches.fetch_and_cache", return_value=[]):
        result = backfill_match(api, _entry())

    assert result["steps"]["registration"] == "created"
    match = TennisScannedMatch.get(TennisScannedMatch.market_id == "EVT1")
    assert match.winner == "a"
    assert match.round == "Final"


def test_idempotent_on_second_run():
    api = MagicMock()
    api.get_trades.return_value = _fake_trades([0.3, 0.5, 0.9])
    with patch("scripts.tennis.backfill_sf_final_matches.populate_weather_for_match", return_value={"skipped": True}), \
         patch("scripts.tennis.backfill_sf_final_matches.fetch_and_cache", return_value=[]):
        backfill_match(api, _entry())
        second = backfill_match(api, _entry())

    assert second["steps"]["registration"] == "already existed"
    assert second["steps"]["ticks"].startswith("already had")
    assert TennisScannedMatch.select().count() == 1


def test_creates_real_probability_ticks_from_trade_tape():
    api = MagicMock()
    api.get_trades.return_value = _fake_trades([0.3, 0.5, 0.9])
    with patch("scripts.tennis.backfill_sf_final_matches.populate_weather_for_match", return_value={"skipped": True}), \
         patch("scripts.tennis.backfill_sf_final_matches.fetch_and_cache", return_value=[]):
        backfill_match(api, _entry())

    match = TennisScannedMatch.get(TennisScannedMatch.market_id == "EVT1")
    ticks = list(TennisMatchProbabilityTick.select().where(TennisMatchProbabilityTick.match == match))
    assert len(ticks) == 3
    assert all(t.model_fair_a is None for t in ticks)  # gap honesto, sin fair price fabricado
    prices = sorted(t.market_price_a for t in ticks)
    assert prices == [0.3, 0.5, 0.9]


def test_saves_stats_with_real_score_when_found():
    api = MagicMock()
    api.get_trades.return_value = _fake_trades([0.3])
    fake_rows = [{"opponent": "Player B", "date": "2026-08-01", "score": "6-4 7-6(5)"}]
    with patch("scripts.tennis.backfill_sf_final_matches.populate_weather_for_match", return_value={"skipped": True}), \
         patch("scripts.tennis.backfill_sf_final_matches.fetch_and_cache", return_value=fake_rows):
        backfill_match(api, _entry())

    stats = TennisMatchStats.get(TennisMatchStats.market_id == "EVT1")
    import json
    data = json.loads(stats.raw_stats_json)
    assert data["match_score"] == "6-4 7-6(5)"
    assert data["match"]["Aces"] == ["3", "1"]


def test_calls_weather_backfill():
    api = MagicMock()
    api.get_trades.return_value = _fake_trades([0.4])
    with patch(
        "scripts.tennis.backfill_sf_final_matches.populate_weather_for_match",
        return_value={"skipped": False, "populated_phases": ["before", "during", "end"]},
    ) as mock_weather, patch("scripts.tennis.backfill_sf_final_matches.fetch_and_cache", return_value=[]):
        result = backfill_match(api, _entry())

    mock_weather.assert_called_once()
    assert result["steps"]["weather"]["populated_phases"] == ["before", "during", "end"]


def test_parse_trades_sorts_chronologically_and_fail_closed_on_bad_fields():
    api = MagicMock()
    api.get_trades.return_value = [
        {"created_time": "2026-08-01T12:00:00Z", "yes_price_dollars": "0.50"},
        {"created_time": "2026-08-01T10:00:00Z", "yes_price_dollars": "0.30"},
        {"created_time": None, "yes_price_dollars": "0.90"},  # descartado
    ]
    parsed = _parse_trades(api, "SOME-TICKER")
    assert len(parsed) == 2
    assert parsed[0][1] == 0.30  # más viejo primero
    assert parsed[1][1] == 0.50


def test_find_real_score_matches_by_opponent_and_date():
    rows = [{"opponent": "Player B", "date": "2026-08-02", "score": "7-5 6-3"}]
    with patch("scripts.tennis.backfill_sf_final_matches.fetch_and_cache", return_value=rows):
        score = _find_real_score("Player A", "Player B", "2026-08-01")  # +/-1 dia tolerancia
    assert score == "7-5 6-3"


def test_find_real_score_fail_closed_no_match():
    with patch("scripts.tennis.backfill_sf_final_matches.fetch_and_cache", return_value=[]):
        assert _find_real_score("Player A", "Player B", "2026-08-01") is None
