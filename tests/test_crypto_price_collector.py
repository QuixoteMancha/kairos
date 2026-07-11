"""Tests for BinanceKlinesClient.get_klines() — real endpoint shape
confirmed live 2026-07-11 (Fase 0 of
docs/crypto-15m-kalshi-strategy1-variant-plan-2026-07-11.md): Binance
/api/v3/klines returns an ARRAY of 12 fields per candle, not a dict."""

from datetime import datetime
from unittest.mock import MagicMock

from src.scrapers.crypto_price_collector import (
    KALSHI_SERIES_TO_BINANCE_SYMBOL, BinanceKlinesClient,
)

# Real shape: [open_time_ms, open, high, low, close, volume, close_time_ms,
# quote_asset_volume, n_trades, taker_buy_base_vol, taker_buy_quote_vol, ignore]
REAL_KLINE = [
    1752198000000, "65000.10", "65100.00", "64950.00", "65050.50", "12.345",
    1752198059999, "803210.5", 421, "6.0", "390000.0", "0",
]


def _client_with_get(payload, raise_exc=None):
    client = BinanceKlinesClient()
    if raise_exc:
        client._get = MagicMock(side_effect=raise_exc)
        return client
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value=payload)
    client._get = MagicMock(return_value=mock_resp)
    return client


def test_get_klines_normalizes_real_array_shape():
    client = _client_with_get([REAL_KLINE])
    klines = client.get_klines("BTCUSDT", interval="1m", limit=1)

    assert len(klines) == 1
    k = klines[0]
    assert k["symbol"] == "BTCUSDT"
    assert isinstance(k["timestamp"], datetime)
    assert k["open"] == 65000.10
    assert k["high"] == 65100.00
    assert k["low"] == 64950.00
    assert k["close"] == 65050.50
    assert k["volume"] == 12.345


def test_get_klines_empty_symbol():
    client = BinanceKlinesClient()
    assert client.get_klines("") == []
    assert client.get_klines(None) == []


def test_get_klines_network_error_fails_closed():
    client = _client_with_get(None, raise_exc=Exception("timeout"))
    assert client.get_klines("BTCUSDT") == []


def test_get_klines_non_list_payload_fails_closed():
    client = _client_with_get({"unexpected": "shape"})
    assert client.get_klines("BTCUSDT") == []


def test_get_klines_malformed_entry_is_skipped_not_fatal():
    bad_entry = [1752198000000, "not-a-number"]  # too short / bad values
    client = _client_with_get([REAL_KLINE, bad_entry])
    klines = client.get_klines("BTCUSDT")
    assert len(klines) == 1


def test_symbol_mapping_covers_all_12_series_explicitly():
    assert len(KALSHI_SERIES_TO_BINANCE_SYMBOL) == 12
    # HYPE documented gap: not on Binance spot, mapped to None on purpose,
    # never silently guessed.
    assert KALSHI_SERIES_TO_BINANCE_SYMBOL["KXHYPE15M"] is None
    assert KALSHI_SERIES_TO_BINANCE_SYMBOL["KXBTC15M"] == "BTCUSDT"
