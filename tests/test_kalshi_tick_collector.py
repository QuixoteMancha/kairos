"""Tests for src/core/orchestration/kalshi_tick_collector.py — Fase 1 of
docs/crypto-15m-kalshi-strategy1-variant-plan-2026-07-11.md. Read-only
discovery + persistence, no network, no real orders."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.database.database import KalshiMarketTick, init_db
from src.core.orchestration.kalshi_tick_collector import (
    CRYPTO_15M_SERIES_PREFIXES,
    collect_ticks_for_ticker,
    discover_open_crypto15m_tickers,
)


@pytest.fixture(autouse=True)
def _clean_table():
    init_db()
    KalshiMarketTick.delete().execute()
    yield
    KalshiMarketTick.delete().execute()


def _fake_market(ticker, event_ticker):
    return {"raw": {"ticker": ticker}, "event_ticker": event_ticker}


def test_discover_filters_to_crypto15m_series_only():
    class _FakeClient:
        def get_markets(self, **kwargs):
            return [
                _fake_market("KXBTC15M-26JUL110345-45", "KXBTC15M-26JUL110345"),
                _fake_market("KXFED-24DEC-T4.50", "KXFED-24DEC"),
                _fake_market("KXETH15M-26JUL110345-40", "KXETH15M-26JUL110345"),
            ]

    tickers = discover_open_crypto15m_tickers(_FakeClient())
    assert tickers == ["KXBTC15M-26JUL110345-45", "KXETH15M-26JUL110345-40"]


def test_discover_fails_closed_on_get_markets_error():
    class _FakeClient:
        def get_markets(self, **kwargs):
            raise Exception("network error")

    assert discover_open_crypto15m_tickers(_FakeClient()) == []


def test_collect_ticks_persists_and_dedups_by_trade_id():
    trade = {
        "trade_id": "tid-1",
        "ticker": "KXBTC15M-26JUL110345-45",
        "event_ticker": "KXBTC15M",
        "created_time": datetime(2026, 7, 11, 3, 45, 12, tzinfo=UTC),
        "yes_price": 0.45,
        "no_price": 0.55,
        "count": 10.0,
        "taker_side": "yes",
    }

    class _FakeClient:
        def get_market_trades(self, **kwargs):
            return [trade]

    client = _FakeClient()
    written_first = collect_ticks_for_ticker(client, "KXBTC15M-26JUL110345-45")
    assert written_first == 1
    assert KalshiMarketTick.select().count() == 1

    # Re-collecting the exact same trade_id must be a silent no-op (dedup),
    # not a duplicate row.
    written_second = collect_ticks_for_ticker(client, "KXBTC15M-26JUL110345-45")
    assert written_second == 0
    assert KalshiMarketTick.select().count() == 1

    row = KalshiMarketTick.get(KalshiMarketTick.trade_id == "tid-1")
    assert row.ticker == "KXBTC15M-26JUL110345-45"
    assert row.yes_price == 0.45
    assert row.fetched_at is not None


def test_collect_ticks_fails_closed_on_client_error():
    class _FakeClient:
        def get_market_trades(self, **kwargs):
            raise Exception("connection reset")

    assert collect_ticks_for_ticker(_FakeClient(), "SOME-TICKER") == 0
    assert KalshiMarketTick.select().count() == 0


def test_collect_ticks_empty_ticker_noop():
    class _FakeClient:
        def get_market_trades(self, **kwargs):
            raise AssertionError("should not be called for an empty ticker")

    assert collect_ticks_for_ticker(_FakeClient(), "") == 0


def test_all_12_series_prefixes_present():
    assert len(CRYPTO_15M_SERIES_PREFIXES) == 12
