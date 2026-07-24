"""
Raw HTTP + parsing for Binance's public klines (candlestick) REST endpoint
-- no API key required. Used as the real underlying-price feed for
`CryptoPriceSnapshot` (database/historical/market_data/), per project
memory (kairos-crypto15m-preevent-momentum-metric-open-question: Binance
underlying used, not Kalshi's own side).

Endpoint: GET https://api.binance.com/api/v3/klines
  params: symbol (e.g. "BTCUSDT"), interval (e.g. "1m"), limit (<=1000)
  response: list of
    [open_time_ms, open, high, low, close, volume, close_time_ms,
     quote_asset_volume, n_trades, taker_buy_base_vol, taker_buy_quote_vol,
     ignore]
  (verified live 2026-07-21)

This module is raw HTTP + parsing only -- no persistence, no business
logic beyond turning Binance's array-of-arrays shape into a list of
plain dicts. Persistence lives in outer_apis/crypto_prices.py.
"""

from datetime import datetime, timezone

import requests

BASE_URL = "https://api.binance.com/api/v3/klines"
DEFAULT_TIMEOUT_S = 10


def fetch_klines(symbol: str, interval: str = "1m", limit: int = 60,
                  timeout_s: int = DEFAULT_TIMEOUT_S) -> list[dict]:
    """Fetches real recent klines for `symbol` (Binance's own format, e.g.
    "BTCUSDT"). Fail-closed: any HTTP/parse error returns an empty list
    instead of raising or fabricating candles -- callers must treat an
    empty list as "no data this cycle for this symbol", not an error to
    propagate and block other symbols."""
    try:
        resp = requests.get(
            BASE_URL,
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=timeout_s,
        )
        resp.raise_for_status()
        raw = resp.json()
    except Exception:
        return []

    if not isinstance(raw, list):
        return []

    candles = []
    for entry in raw:
        try:
            open_time_ms = entry[0]
            candles.append({
                "timestamp": datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc),
                "open": float(entry[1]),
                "high": float(entry[2]),
                "low": float(entry[3]),
                "close": float(entry[4]),
                "volume": float(entry[5]),
            })
        except (IndexError, TypeError, ValueError):
            continue
    return candles
