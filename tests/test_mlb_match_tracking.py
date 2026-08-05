"""Tests del registro + ticks de probabilidad tick-by-tick para MLB
(2026-08-05, pedido explícito del usuario -- mismo patrón que
`tests/test_tennis_match_tracking.py`)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from database.db import init_db_historical, db_historical
from database.historical.team_sports.mlbscannedmatch import MlbScannedMatch
from database.historical.team_sports.mlbmatchprobabilitytick import MlbMatchProbabilityTick
from database.historical.team_sports.mlbmatchtracking import (
    register_scanned_mlb_match, register_scanned_mlb_matches, get_open_scanned_mlb_matches,
    save_mlb_probability_tick, mark_mlb_match_settled, filter_new_mlb_markets,
)


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        MlbMatchProbabilityTick.delete().execute()
        MlbScannedMatch.delete().execute()
    yield
    with db_historical.connection_context():
        MlbMatchProbabilityTick.delete().execute()
        MlbScannedMatch.delete().execute()


def _fake_market(market_id="KXMLBGAME-TEST", **overrides):
    base = {
        "id": market_id, "ticker_a": f"{market_id}-A", "ticker_b": f"{market_id}-B",
        "team_a_name": "Yankees", "team_b_name": "Red Sox",
        "team_a_rating": 1550.0, "team_b_rating": 1480.0,
        "close_time": "2026-08-01T00:00:00Z",
    }
    base.update(overrides)
    return base


def test_register_scanned_mlb_match_is_idempotent():
    register_scanned_mlb_match(_fake_market())
    register_scanned_mlb_match(_fake_market())
    assert MlbScannedMatch.select().count() == 1


def test_register_scanned_mlb_match_skips_incomplete_data():
    register_scanned_mlb_match(_fake_market(team_a_name=None))
    assert MlbScannedMatch.select().count() == 0


def test_filter_new_mlb_markets_excludes_already_registered():
    register_scanned_mlb_match(_fake_market("KXMLBGAME-OLD"))
    markets = [_fake_market("KXMLBGAME-OLD"), _fake_market("KXMLBGAME-NEW")]
    new = filter_new_mlb_markets(markets)
    assert [m["id"] for m in new] == ["KXMLBGAME-NEW"]


def test_get_open_scanned_mlb_matches_excludes_settled():
    register_scanned_mlb_match(_fake_market("KXMLBGAME-OPEN"))
    register_scanned_mlb_match(_fake_market("KXMLBGAME-SETTLED"))
    mark_mlb_match_settled("KXMLBGAME-SETTLED", "a")

    open_matches = get_open_scanned_mlb_matches()
    assert len(open_matches) == 1
    assert open_matches[0]["market_id"] == "KXMLBGAME-OPEN"


def test_save_mlb_probability_tick_requires_registered_match():
    save_mlb_probability_tick("NOT_REGISTERED", 0.5, 0.52)
    assert MlbMatchProbabilityTick.select().count() == 0


def test_save_mlb_probability_tick_persists_both_prices():
    register_scanned_mlb_match(_fake_market("KXMLBGAME-TICK"))
    save_mlb_probability_tick("KXMLBGAME-TICK", market_price_a=0.55, model_fair_a=0.58)

    tick = MlbMatchProbabilityTick.select().first()
    assert tick.market_price_a == 0.55
    assert tick.model_fair_a == 0.58
    assert tick.match.market_id == "KXMLBGAME-TICK"


def test_mark_mlb_match_settled_only_accepts_a_or_b():
    register_scanned_mlb_match(_fake_market("KXMLBGAME-BAD"))
    mark_mlb_match_settled("KXMLBGAME-BAD", "invalid")
    match = MlbScannedMatch.get(MlbScannedMatch.market_id == "KXMLBGAME-BAD")
    assert match.winner is None
    assert match.settled_at is None


def test_tick_worker_winner_logic_and_batching():
    from workers import mlb_match_tick_worker as worker

    register_scanned_mlb_match(_fake_market("KXMLBGAME-W1"))
    register_scanned_mlb_match(_fake_market("KXMLBGAME-W2"))

    fake_cache = {
        "KXMLBGAME-W1-A": {"current_a_price": None, "current_b_price": None, "raw": {"result": "yes"}},
        "KXMLBGAME-W1-B": {"current_a_price": None, "current_b_price": None, "raw": {"result": "no"}},
        "KXMLBGAME-W2-A": {"current_a_price": 0.61, "current_b_price": 0.42, "raw": {}},
        "KXMLBGAME-W2-B": {"current_a_price": 0.42, "current_b_price": 0.61, "raw": {}},
    }

    with patch("workers.mlb_match_tick_worker.KalshiAPI") as mock_api_cls, \
         patch.object(worker._fmodel, "calculate_fair_price", return_value={"fair_a_price": 0.6, "fair_b_price": 0.4}), \
         patch.object(worker, "_register_new_mlb_matches", return_value=0):
        mock_api = mock_api_cls.return_value
        mock_api.get_markets_by_tickers.return_value = fake_cache
        result = worker.run_once()

    assert result["settled"] == 1
    assert result["ticked"] == 1
    assert result["errors"] == 0

    w1 = MlbScannedMatch.get(MlbScannedMatch.market_id == "KXMLBGAME-W1")
    assert w1.winner == "a"  # ticker_a resolvió 'yes' -> ganó team_a

    tick = MlbMatchProbabilityTick.select().first()
    assert tick.match.market_id == "KXMLBGAME-W2"
    assert tick.market_price_a == 0.61


def test_tick_worker_registers_new_markets_before_ticking():
    from workers import mlb_match_tick_worker as worker

    with patch("workers.mlb_match_tick_worker.KalshiAPI") as mock_api_cls, \
         patch("workers.mlb_match_tick_worker.build_team_sport_markets", return_value=[_fake_market("KXMLBGAME-NEW")]):
        mock_api = mock_api_cls.return_value
        mock_api.get_markets.return_value = []
        mock_api.get_markets_by_tickers.return_value = {}
        result = worker.run_once()

    assert result["registered"] == 1
    assert MlbScannedMatch.select().where(MlbScannedMatch.market_id == "KXMLBGAME-NEW").exists()
