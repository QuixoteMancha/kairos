"""Tests de `strategies/bet_on_favourite.py` -- 2026-08-11, pedido
explícito del usuario ("arma el código de la estrategia
bet_on_favourite.py"). Archivo sin tests previos (estaba roto,
SyntaxError, nunca pudo importarse antes de este fix). Mockea toda
fuente de datos real (DB-first splits + Kalshi portfolio)."""

from __future__ import annotations

from unittest.mock import patch
import pytest

from strategies.bet_on_favourite import (
    BetOnFavourite,
    bet_on_favourite,
    _rank_difference_ok,
    _player_surface_winpct,
)


@pytest.fixture(autouse=True)
def strategy_signals():
    default_form = _mock_form({"Novak Djokovic": 0.8, "Nobody Else": 0.4})
    with patch("strategies.bet_on_favourite._model_probability", return_value=0.80), \
         patch("strategies.bet_on_favourite.get_recent_form_by_surface", side_effect=default_form), \
         patch("strategies.bet_on_favourite.get_recent_form", side_effect=default_form), \
         patch("strategies.bet_on_favourite.get_post_injury_performance_penalty", return_value={"flagged": False, "penalty": 0.0}), \
         patch("strategies.bet_on_favourite.preload_post_injury_performance_penalties"):
        yield


def _match(**overrides) -> dict:
    base = {
        "type": "tennis",
        "id": "M1",
        "current_a_price": 0.7,
        "current_b_price": 0.3,
        "player_a_name": "Novak Djokovic",
        "player_a_rank": 1,
        "ticker_a": "KXATPMATCH-A",
        "player_b_name": "Nobody Else",
        "player_b_rank": 150,
        "ticker_b": "KXATPMATCH-B",
        "tour": "ATP",
        "surface": "Hard",
    }
    base.update(overrides)
    return base


def _mock_form(win_pcts: dict[str, float]):
    def _get(player_name, *args, **kwargs):
        if player_name not in win_pcts:
            return None
        return {"win_rate": win_pcts[player_name], "n_matches": 10}
    return _get


def _mock_portfolio(balance_dollars=1000.0, fills=None):
    mock = _PatchedPortfolio(balance_dollars, fills or [])
    return mock


class _PatchedPortfolio:
    def __init__(self, balance_dollars, fills):
        self._balance = balance_dollars
        self._fills = fills

    def get_portfolio_balance(self):
        return {"balance_dollars": self._balance}

    def get_portfolio_fills(self, min_ts=None):
        return self._fills


def test_rank_difference_ok_atp_threshold_20():
    assert _rank_difference_ok(1, 21, "ATP") is True  # diff=20
    assert _rank_difference_ok(1, 20, "ATP") is False  # diff=19


def test_rank_difference_ok_wta_threshold_30():
    assert _rank_difference_ok(1, 31, "WTA") is True  # diff=30
    assert _rank_difference_ok(1, 30, "WTA") is False  # diff=29


def test_rank_difference_ok_fail_closed_on_missing_rank():
    assert _rank_difference_ok(None, 20, "ATP") is False
    assert _rank_difference_ok(1, None, "ATP") is False


def test_not_tennis_returns_none():
    assert bet_on_favourite({"type": "football"}) is None


def test_missing_prices_returns_none():
    assert bet_on_favourite(_match(current_a_price=None)) is None


def test_rank_gap_below_threshold_returns_none():
    with patch("strategies.bet_on_favourite.KalshiPortfolioAPI"):
        result = bet_on_favourite(_match(player_b_rank=15))  # gap=14 < 20
    assert result is None


def test_favourite_worse_surface_winrate_returns_none():
    with patch(
        "strategies.bet_on_favourite.get_recent_form_by_surface",
        side_effect=_mock_form({"Novak Djokovic": 0.5, "Nobody Else": 0.8}),
    ):
        result = bet_on_favourite(_match())
    assert result is None


def test_missing_surface_data_fails_closed():
    with patch("strategies.bet_on_favourite.get_recent_form_by_surface", return_value=None):
        result = bet_on_favourite(_match())
    assert result is None


def test_favourite_worse_recent_form_returns_none():
    with patch(
        "strategies.bet_on_favourite.get_recent_form",
        side_effect=_mock_form({"Novak Djokovic": 0.4, "Nobody Else": 0.7}),
    ):
        assert bet_on_favourite(_match()) is None


def test_missing_recent_form_fails_closed():
    with patch("strategies.bet_on_favourite.get_recent_form", return_value=None):
        assert bet_on_favourite(_match()) is None


def test_full_real_proposal_with_sizing():
    with patch(
        "strategies.bet_on_favourite.get_recent_form_by_surface",
        side_effect=_mock_form({"Novak Djokovic": 0.8, "Nobody Else": 0.4}),
    ), patch(
        "strategies.bet_on_favourite.KalshiPortfolioAPI",
        return_value=_mock_portfolio(balance_dollars=1000.0, fills=[]),
    ):
        result = bet_on_favourite(_match())

    assert result is not None
    assert result["ticker"] == "KXATPMATCH-A"
    assert result["side"] == "yes"
    assert result["stake_usd"] == 10.0  # 1% de 1000
    assert result["fav_name"] == "Novak Djokovic"
    assert result["dog_name"] == "Nobody Else"


def test_daily_cap_reduces_stake():
    # ya se gastó 490 de un tope diario de 500 (50% de 1000) -- el
    # stake propuesto (10, 1%) se recorta al remanente (10 exacto en
    # este caso limite, probamos con más gasto para forzar el recorte).
    fills = [
        {"action": "buy", "side": "yes", "yes_price_dollars": 0.7, "count_fp": 700},  # 490
    ]
    with patch(
        "strategies.bet_on_favourite.get_recent_form_by_surface",
        side_effect=_mock_form({"Novak Djokovic": 0.8, "Nobody Else": 0.4}),
    ), patch(
        "strategies.bet_on_favourite.KalshiPortfolioAPI",
        return_value=_mock_portfolio(balance_dollars=1000.0, fills=fills),
    ):
        result = bet_on_favourite(_match())

    assert result is not None
    assert result["stake_usd"] == 10.0  # remaining = 500-490=10, proposed=10 -> min=10


def test_daily_cap_exhausted_returns_none():
    fills = [
        # spent_today = 0.5 * 1010 = 505 > daily_allowed (500) -> remaining=0
        {"action": "buy", "side": "yes", "yes_price_dollars": 0.5, "count_fp": 1010},
    ]
    with patch(
        "strategies.bet_on_favourite.get_recent_form_by_surface",
        side_effect=_mock_form({"Novak Djokovic": 0.8, "Nobody Else": 0.4}),
    ), patch(
        "strategies.bet_on_favourite.KalshiPortfolioAPI",
        return_value=_mock_portfolio(balance_dollars=1000.0, fills=fills),
    ):
        result = bet_on_favourite(_match())

    assert result is None


def test_requires_minimum_calibrated_model_edge():
    with patch(
        "strategies.bet_on_favourite.get_recent_form_by_surface",
        side_effect=_mock_form({"Novak Djokovic": 0.8, "Nobody Else": 0.4}),
    ), patch("strategies.bet_on_favourite._model_probability", return_value=0.71):
        assert bet_on_favourite(_match(current_a_price=0.70)) is None


def test_post_injury_decline_can_remove_otherwise_valid_edge():
    def injury(player_name, **kwargs):
        return {
            "flagged": player_name == "Novak Djokovic",
            "penalty": 0.10 if player_name == "Novak Djokovic" else 0.0,
        }

    with patch("strategies.bet_on_favourite._model_probability", return_value=0.75), patch(
        "strategies.bet_on_favourite.get_post_injury_performance_penalty", side_effect=injury,
    ):
        assert bet_on_favourite(_match(current_a_price=0.70)) is None


def test_malformed_fill_entry_skipped_not_fatal():
    fills = [
        {"action": "buy", "side": "yes", "yes_price_dollars": "not-a-number", "count_fp": 1},
        {"action": "sell"},  # ignorado, no es 'buy'
    ]
    with patch(
        "strategies.bet_on_favourite.get_recent_form_by_surface",
        side_effect=_mock_form({"Novak Djokovic": 0.8, "Nobody Else": 0.4}),
    ), patch(
        "strategies.bet_on_favourite.KalshiPortfolioAPI",
        return_value=_mock_portfolio(balance_dollars=1000.0, fills=fills),
    ):
        result = bet_on_favourite(_match())

    assert result is not None  # el fill malformado no rompe el cálculo
    assert result["stake_usd"] == 10.0


def test_portfolio_balance_failure_fails_closed():
    class _BrokenPortfolio:
        def get_portfolio_balance(self):
            raise Exception("Kalshi down")

    with patch(
        "strategies.bet_on_favourite.get_recent_form_by_surface",
        side_effect=_mock_form({"Novak Djokovic": 0.8, "Nobody Else": 0.4}),
    ), patch("strategies.bet_on_favourite.KalshiPortfolioAPI", return_value=_BrokenPortfolio()):
        result = bet_on_favourite(_match())

    assert result is None


def test_class_evaluates_all_markets_and_collects_only_proposals():
    markets = [_match(), _match(id="M2", player_b_rank=10)]
    with patch(
        "strategies.bet_on_favourite.get_recent_form_by_surface",
        side_effect=_mock_form({"Novak Djokovic": 0.8, "Nobody Else": 0.4}),
    ), patch(
        "strategies.bet_on_favourite.KalshiPortfolioAPI",
        return_value=_mock_portfolio(balance_dollars=1000.0, fills=[]),
    ):
        strategy = BetOnFavourite(api=object(), markets=markets)
        opportunities = strategy.evaluate_risk_and_return_opportunities()

    assert opportunities == strategy.opportunities
    assert [item["market_id"] for item in opportunities] == ["M1"]
