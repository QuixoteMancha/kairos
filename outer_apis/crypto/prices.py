"""
Public call surface + persistence for real underlying crypto OHLCV data,
sourced from Binance's public REST API (outer_apis/scrapers/crypto/
binance_klines_scraper.py does the raw HTTP+parsing; this module owns
symbol list + persistence into `CryptoPriceSnapshot`).

Symbol format: Binance's own native pair format (e.g. "BTCUSDT") --
`CryptoPriceSnapshot.symbol` is a plain CharField with no format
constraint in the model, and the locally-rescued dev DB has zero rows to
confirm the historical format against (real rows only live in the mini-pc
production volume, out of scope to touch here). Binance's native format
is used as the least-lossy choice: if production data actually used a
different format (e.g. "BTC-USD"), reconcile before wiring this into
anything that joins against old rows.

Deduped on (symbol, timestamp) -- no unique index confirmed on the
rescued table (see cryptopricesnapshot.py's Meta), so this does an
existence check before each insert.
"""

from datetime import datetime, timezone
from typing import Any

from outer_apis.scrapers.crypto.binance_klines_scraper import fetch_klines
from database.db import init_db_historical
from database.historical.market_data.cryptopricesnapshot import CryptoPriceSnapshot

# Matches the crypto series Kairos already trades against via Kalshi
# crypto_15m markets (outer_apis/kalshi_ticks_collector.py's
# CRYPTO_15M_SERIES_TICKERS) plus the 2 extra symbols the rescued
# CryptoPriceSnapshot data covers (11 total per project memory) that
# don't (yet) have a matching Kalshi crypto_15m series.
BINANCE_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "BNBUSDT",
    "ZECUSDT",
    "NEARUSDT",
    "HYPEUSDT",
    "ADAUSDT",
    "LTCUSDT",
]


def _now():
    return datetime.now(timezone.utc)


def collect_real_prices(symbols: list[str] | None = None, interval: str = "1m",
                         limit: int = 60) -> dict[str, int]:
    """Fetches real recent klines for every symbol in `symbols` (defaults
    to BINANCE_SYMBOLS) and persists new candles (deduped on symbol+
    timestamp) into CryptoPriceSnapshot. Fail-closed per symbol: a bad/
    delisted symbol just yields zero candles for it, never blocks the
    rest. Returns {"symbols_scanned", "candles_seen", "rows_inserted"}."""
    stats = {"symbols_scanned": 0, "candles_seen": 0, "rows_inserted": 0}
    symbols = symbols or BINANCE_SYMBOLS

    try:
        init_db_historical()
    except Exception:
        return stats

    fetched_now = _now()
    for symbol in symbols:
        stats["symbols_scanned"] += 1
        try:
            candles = fetch_klines(symbol, interval=interval, limit=limit)
        except Exception:
            continue

        stats["candles_seen"] += len(candles)
        for candle in candles:
            row = _candle_to_row(symbol, candle, fetched_now)
            if row is None:
                continue
            try:
                exists = (
                    CryptoPriceSnapshot
                    .select()
                    .where(
                        (CryptoPriceSnapshot.symbol == row["symbol"])
                        & (CryptoPriceSnapshot.timestamp == row["timestamp"])
                    )
                    .exists()
                )
                if exists:
                    continue
                CryptoPriceSnapshot.create(**row)
                stats["rows_inserted"] += 1
            except Exception:
                continue

    return stats


def _candle_to_row(symbol: str, candle: dict[str, Any], fetched_at) -> dict[str, Any] | None:
    try:
        return {
            "created_at": fetched_at,
            "updated_at": fetched_at,
            "symbol": symbol,
            "timestamp": candle["timestamp"],
            "open": candle["open"],
            "high": candle["high"],
            "low": candle["low"],
            "close": candle["close"],
            "volume": candle["volume"],
        }
    except KeyError:
        return None
