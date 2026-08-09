"""Tests del backtest con precio de mercado real (2026-08-03, pedido
explícito del usuario -- sugerencia de mejora aplicada en código,
backtest real pendiente hasta completar el backfill de datos)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis import MatchHistory, TennisHistoricalOdds
from scripts.tennis.real_market_odds_backtest import (
    _devig_prob, _load_priced_matches, calibration_by_real_odds, run_strategy1_with_real_odds,
)

_next_match_id = iter(range(1, 10_000))


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        MatchHistory.delete().where(MatchHistory.source == "test_real_odds").execute()
        TennisHistoricalOdds.delete().where(TennisHistoricalOdds.source == "test_real_odds").execute()
    yield
    with db_historical.connection_context():
        MatchHistory.delete().where(MatchHistory.source == "test_real_odds").execute()
        TennisHistoricalOdds.delete().where(TennisHistoricalOdds.source == "test_real_odds").execute()


def test_devig_prob_normalizes_to_100pct():
    p = _devig_prob(1.5, 3.0)
    # crudo: 1/1.5=0.6667, 1/3.0=0.3333, suma=1.0 (sin vig en este caso de prueba)
    assert p == pytest.approx(0.6667, abs=0.001)


def test_devig_prob_removes_real_vig():
    # cuotas reales con margen: 1/1.9 + 1/2.0 = 0.5263+0.5 = 1.0263 (>100%)
    p = _devig_prob(1.9, 2.0)
    assert p is not None
    p_opp = _devig_prob(2.0, 1.9)
    assert round(p + p_opp, 4) == 1.0  # de-vigged, suman exactamente 100%


def test_devig_prob_fail_closed_on_invalid_odds():
    assert _devig_prob(None, 2.0) is None
    assert _devig_prob(0.5, 2.0) is None  # cuota <=1.0 imposible
    assert _devig_prob(1.5, None) is None


def _seed_match(player, opponent, date, result, odds_self, odds_opp, tournament="X"):
    # 2026-08-09: `TennisMatchHistory` (1 fila por jugador) reemplazada
    # por `MatchHistory` (1 fila por partido) -- `result` ("W"/"L") es
    # desde la perspectiva de `player`, se traduce a winner_side "a"/"b".
    MatchHistory.create(
        match_id=f"test:{next(_next_match_id)}",
        player_a_name=player, player_b_name=opponent, match_date=date,
        winner_side=("a" if result == "W" else "b"),
        score="6-4 6-3", source="test_real_odds", fetched_at=datetime.now(timezone.utc),
        tournament=tournament,
    )
    TennisHistoricalOdds.create(
        player_name=player, opponent_name=opponent, match_date=date,
        odds_self=odds_self, odds_opponent=odds_opp,
        source="test_real_odds", fetched_at=datetime.now(timezone.utc),
    )


def test_load_priced_matches_identifies_favorite_by_lowest_odds():
    with db_historical.connection_context():
        _seed_match("A", "B", "2026-01-01", "W", odds_self=1.5, odds_opp=2.8)  # A favorito (cuota más baja), gana
    matches = _load_priced_matches()
    assert len(matches) == 1
    assert matches[0]["favorite_won"] is True
    assert matches[0]["favorite_fair_price"] > 0.5


def test_load_priced_matches_favorite_lost():
    with db_historical.connection_context():
        _seed_match("A", "B", "2026-01-01", "L", odds_self=1.5, odds_opp=2.8)  # A favorito, PIERDE
    matches = _load_priced_matches()
    assert matches[0]["favorite_won"] is False


def test_load_priced_matches_skips_rows_without_odds():
    with db_historical.connection_context():
        MatchHistory.create(
            match_id=f"test:{next(_next_match_id)}",
            player_a_name="NoOdds", player_b_name="X", match_date="2026-01-01", winner_side="a",
            score="6-4 6-3", source="test_real_odds", fetched_at=datetime.now(timezone.utc),
        )
    matches = _load_priced_matches(tour_players=["NoOdds"])
    assert matches == []


def test_calibration_by_real_odds_buckets_correctly():
    matches = [
        {"favorite_fair_price": 0.60, "favorite_won": True},
        {"favorite_fair_price": 0.61, "favorite_won": False},
        {"favorite_fair_price": 0.85, "favorite_won": True},
    ]
    result = calibration_by_real_odds(matches)
    assert result["0.60-0.65"]["n"] == 2
    assert result["0.60-0.65"]["actual_hit_rate"] == 0.5
    assert result["0.85-0.90"]["n"] == 1
    assert result["0.85-0.90"]["actual_hit_rate"] == 1.0


def test_run_strategy1_with_real_odds_applies_fees_and_reports_metrics():
    matches = [
        {"favorite_fair_price": 0.60, "favorite_won": True},
        {"favorite_fair_price": 0.60, "favorite_won": False},
    ]
    result = run_strategy1_with_real_odds(matches, initial_capital=50.0)
    assert result["n_trades"] == 2
    assert result["winrate"] == 0.5
    # con fee real, la ganancia neta debe ser MENOR que el bruto (1-0.6)/0.6=0.667
    assert result["avg_roi_per_trade_pct"] is not None
    assert result["final_capital_usdc"] != 50.0


def test_run_strategy1_with_real_odds_no_trades_returns_none_metrics():
    result = run_strategy1_with_real_odds([], initial_capital=50.0)
    assert result["n_trades"] == 0
    assert result["winrate"] is None
    assert result["sharpe_ratio_per_trade"] is None
    assert result["final_capital_usdc"] == 50.0
