"""Tests del scanner de MarketArb vía websocket (2026-08-02, pedido
explícito del usuario). Mockea toda request real de Kalshi."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from database.db import init_db, db
from database.accounting.marketarbwebsocketsignal import MarketArbWebsocketSignal
from workers.market_arb_websocket_scanner import (
    _category_for_ticker, build_market_registry, _refresh_and_check,
)


@pytest.fixture(autouse=True)
def clean_db():
    init_db()
    with db.connection_context():
        MarketArbWebsocketSignal.delete().execute()
    yield
    with db.connection_context():
        MarketArbWebsocketSignal.delete().execute()


def test_category_for_ticker_matches_known_prefixes():
    assert _category_for_ticker("KXBTC15M-26AUG020600-00") == "crypto"
    assert _category_for_ticker("KXHIGHNY-26AUG03-T87") == "weather"
    assert _category_for_ticker("KXATPMATCH-26AUG02PEGEAL-PEG") == "tennis"
    assert _category_for_ticker("KXMLBGAME-26AUG02-X") == "team_sport"


def test_category_for_ticker_unknown_prefix():
    assert _category_for_ticker("SOMETHING-ELSE") == "unknown"


def _fake_api_with_series(markets_by_series):
    api = MagicMock()
    api.get_markets.side_effect = lambda series_ticker, **kw: markets_by_series.get(series_ticker, [])
    return api


def test_build_market_registry_indexes_crypto_and_weather_as_same_ticket():
    crypto_market = {"id": "KXBTC15M-X", "type": "crypto", "current_a_price": 0.5, "current_b_price": 0.51}
    weather_market = {"id": "KXHIGHNY-X", "type": "weather", "current_a_price": 0.3, "current_b_price": 0.68}

    api = _fake_api_with_series({
        "KXBTC15M": [crypto_market], "KXETH15M": [], "KXSOL15M": [],
        "KXHIGHNY": [weather_market], "KXHIGHCHI": [], "KXHIGHAUS": [],
        "KXHIGHMIA": [], "KXHIGHDEN": [], "KXHIGHPHIL": [],
    })

    with patch("workers.market_arb_websocket_scanner.build_tennis_markets", return_value=[]), \
         patch("workers.market_arb_websocket_scanner.build_football_markets", return_value=[]), \
         patch("workers.market_arb_websocket_scanner.build_team_sport_markets", return_value=[]):
        registry = build_market_registry(api)

    assert registry["KXBTC15M-X"]["category"] == "crypto"
    assert registry["KXBTC15M-X"]["ticker_a"] == registry["KXBTC15M-X"]["ticker_b"] == "KXBTC15M-X"
    assert registry["KXHIGHNY-X"]["category"] == "weather"


def test_build_market_registry_indexes_both_legs_of_cross_ticket_pair():
    tennis_market = {
        "id": "EVT1", "ticker_a": "EVT1-A", "ticker_b": "EVT1-B",
        "type": "tennis", "current_a_price": 0.4, "current_b_price": 0.55,
    }
    api = _fake_api_with_series({
        "KXATPMATCH": [], "KXWTAMATCH": [],
        "KXBTC15M": [], "KXETH15M": [], "KXSOL15M": [],
        "KXHIGHNY": [], "KXHIGHCHI": [], "KXHIGHAUS": [],
        "KXHIGHMIA": [], "KXHIGHDEN": [], "KXHIGHPHIL": [],
    })
    with patch("workers.market_arb_websocket_scanner.build_tennis_markets", return_value=[tennis_market]), \
         patch("workers.market_arb_websocket_scanner.build_football_markets", return_value=[]), \
         patch("workers.market_arb_websocket_scanner.build_team_sport_markets", return_value=[]):
        registry = build_market_registry(api)

    assert registry["EVT1-A"] is registry["EVT1-B"]  # mismo objeto, ambas patas apuntan al mismo mercado
    assert registry["EVT1-A"]["category"] == "tennis"


def test_refresh_and_check_saves_real_opportunity():
    market = {
        "id": "EVT1", "ticker_a": "EVT1-A", "ticker_b": "EVT1-B",
        "type": "tennis", "category": "tennis",
        "current_a_price": 0.9, "current_b_price": 0.9,  # stale, se sobreescribe
    }
    fresh_data = {
        "EVT1-A": {"current_a_price": 0.30},
        "EVT1-B": {"current_a_price": 0.30},  # suma 0.60 + fees < 1 -> arb real
    }
    api = MagicMock()
    api.get_markets_by_tickers.return_value = fresh_data

    _refresh_and_check(api, market)

    signal = MarketArbWebsocketSignal.get(MarketArbWebsocketSignal.market_id == "EVT1")
    assert signal.category == "tennis"
    assert signal.price_a == 0.30
    assert signal.price_b == 0.30
    assert signal.return_opportunity > 0


def test_refresh_and_check_no_signal_when_no_real_arb():
    market = {
        "id": "EVT2", "ticker_a": "EVT2-A", "ticker_b": "EVT2-B",
        "type": "tennis", "category": "tennis",
        "current_a_price": 0.5, "current_b_price": 0.5,
    }
    fresh_data = {
        "EVT2-A": {"current_a_price": 0.55},
        "EVT2-B": {"current_a_price": 0.55},  # suma 1.10, sin arb
    }
    api = MagicMock()
    api.get_markets_by_tickers.return_value = fresh_data

    _refresh_and_check(api, market)

    assert MarketArbWebsocketSignal.select().where(MarketArbWebsocketSignal.market_id == "EVT2").count() == 0


def test_refresh_and_check_fail_closed_when_leg_gone():
    market = {
        "id": "EVT3", "ticker_a": "EVT3-A", "ticker_b": "EVT3-B",
        "type": "tennis", "category": "tennis",
        "current_a_price": 0.4, "current_b_price": 0.4,
    }
    api = MagicMock()
    api.get_markets_by_tickers.return_value = {"EVT3-A": {"current_a_price": 0.3}}  # falta EVT3-B

    _refresh_and_check(api, market)  # no debe lanzar excepción

    assert MarketArbWebsocketSignal.select().where(MarketArbWebsocketSignal.market_id == "EVT3").count() == 0


def test_refresh_and_check_same_ticket_uses_current_b_from_same_leg():
    market = {
        "id": "KXBTC15M-Y", "ticker_a": "KXBTC15M-Y", "ticker_b": "KXBTC15M-Y",
        "type": "crypto", "category": "crypto",
        "current_a_price": 0.5, "current_b_price": 0.5,
    }
    api = MagicMock()
    api.get_markets_by_tickers.return_value = {
        "KXBTC15M-Y": {"current_a_price": 0.30, "current_b_price": 0.30},
    }

    _refresh_and_check(api, market)

    signal = MarketArbWebsocketSignal.get(MarketArbWebsocketSignal.market_id == "KXBTC15M-Y")
    assert signal.price_a == 0.30
    assert signal.price_b == 0.30
