"""Tests for KalshiClient.get_market_trades() — real endpoint shape confirmed
live 2026-07-11 against external-api.kalshi.com/trade-api/v2/markets/trades
(Fase 0 of docs/crypto-15m-kalshi-strategy1-variant-plan-2026-07-11.md)."""

from datetime import datetime
from unittest.mock import MagicMock

from src.scrapers.kalshi_client import KalshiClient

REAL_TRADE = {
    "trade_id": "abc-123",
    "ticker": "KXBTC15M-26JUL110345-45",
    "created_time": "2026-07-11T03:45:12Z",
    "yes_price_dollars": "0.4500",
    "no_price_dollars": "0.5500",
    "count_fp": "10.0000",
    "taker_side": "yes",
    "taker_outcome_side": "yes",
    "taker_book_side": "yes",
    "is_block_trade": False,
}


def _client_with_pages(pages, raise_exc=None):
    client = KalshiClient()
    if raise_exc:
        client._get = MagicMock(side_effect=raise_exc)
        return client
    responses = []
    for payload in pages:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value=payload)
        responses.append(mock_resp)
    client._get = MagicMock(side_effect=responses)
    return client


def test_get_market_trades_normalizes_real_shape():
    client = _client_with_pages([{"cursor": "", "trades": [REAL_TRADE]}])
    trades = client.get_market_trades(ticker="KXBTC15M-26JUL110345-45")

    assert len(trades) == 1
    t = trades[0]
    assert t["trade_id"] == "abc-123"
    assert t["ticker"] == "KXBTC15M-26JUL110345-45"
    assert t["event_ticker"] == "KXBTC15M"
    assert isinstance(t["created_time"], datetime)
    assert t["yes_price"] == 0.45
    assert t["no_price"] == 0.55
    assert t["count"] == 10.0
    assert t["taker_side"] == "yes"


def test_get_market_trades_paginates_via_cursor():
    page1 = {"cursor": "next-page-token", "trades": [REAL_TRADE]}
    page2 = {"cursor": "", "trades": [{**REAL_TRADE, "trade_id": "abc-124"}]}
    client = _client_with_pages([page1, page2])
    trades = client.get_market_trades(ticker="KXBTC15M-26JUL110345-45")
    assert len(trades) == 2
    assert {t["trade_id"] for t in trades} == {"abc-123", "abc-124"}


def test_get_market_trades_empty_cursor_stops_pagination():
    client = _client_with_pages([{"cursor": "", "trades": []}])
    trades = client.get_market_trades(ticker="SOME-TICKER")
    assert trades == []


def test_get_market_trades_stops_at_max_pages():
    # Every page reports a non-empty cursor (would loop forever without
    # the MAX_TRADE_PAGES guard).
    pages = [{"cursor": "still-more", "trades": [REAL_TRADE]}] * (KalshiClient.MAX_TRADE_PAGES + 3)
    client = _client_with_pages(pages)
    trades = client.get_market_trades(ticker="SOME-TICKER")
    assert len(trades) == KalshiClient.MAX_TRADE_PAGES


def test_get_market_trades_network_error_fails_closed():
    client = _client_with_pages([], raise_exc=Exception("connection reset"))
    trades = client.get_market_trades(ticker="SOME-TICKER")
    assert trades == []


def test_get_market_trades_malformed_trade_is_skipped_not_fatal():
    payload = {
        "cursor": "",
        "trades": [REAL_TRADE, {"ticker": "MISSING-TRADE-ID"}, None],
    }
    client = _client_with_pages([payload])
    trades = client.get_market_trades(ticker="KXBTC15M-26JUL110345-45")
    assert len(trades) == 1
    assert trades[0]["trade_id"] == "abc-123"
