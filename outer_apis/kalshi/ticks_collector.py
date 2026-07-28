"""
Real-tick collector for `KalshiMarketTick` (database/historical/market_data/).

Fetches REAL per-trade ticks (`GET /trade-api/v2/markets/trades`, via
`KalshiAPI.get_trades`) for currently-open crypto_15m markets and persists
new rows into the historical DB. Fail-closed by design: a failure fetching
or persisting trades for one ticker never stops the others, and nothing is
ever fabricated -- a ticker with no fetchable trades just contributes zero
rows.

Deduped on `trade_id` (no unique index on the rescued table -- confirmed
by reading its Peewee `Meta`, see kalshimarkettick.py -- so this does an
existence check per trade before inserting rather than assuming an upsert
is available cheaply).

Which markets to scan: `KalshiAPI.get_markets(series_ticker=...)` for each
known crypto_15m series ticker (matches `_infer_category`'s crypto
prefixes in outer_apis/kalshi.py: KXBTC/KXETH/KXSOL/KXXRP/KXDOGE/KXBNB, plus
KXZEC/KXNEAR/KXHYPE seen in the rescued KalshiMarketTick data but not yet
in `_infer_category` -- kept as a local list here instead of touching that
shared heuristic, which is out of scope for this collector).
"""

from datetime import datetime, timezone
from typing import Any

from outer_apis.kalshi.client import KalshiAPI
from database.db import init_db_historical
from database.historical.market_data.kalshimarkettick import KalshiMarketTick

# Series tickers observed for Kalshi's 15-minute crypto up/down markets.
# "15M" suffix is the real naming pattern seen live 2026-07-21
# (e.g. KXBTC15M) -- matches the crypto symbols already present in the
# rescued KalshiMarketTick data (BTC/ETH/SOL/XRP/DOGE/BNB/ZEC/NEAR/HYPE).
CRYPTO_15M_SERIES_TICKERS = [
    "KXBTC15M",
    "KXETH15M",
    "KXSOL15M",
    "KXXRP15M",
    "KXDOGE15M",
    "KXBNB15M",
    "KXZEC15M",
    "KXNEAR15M",
    "KXHYPE15M",
]


def _now():
    return datetime.now(timezone.utc)


def _open_crypto_15m_tickers(api: KalshiAPI) -> list[dict[str, Any]]:
    """Real currently-open/active crypto_15m markets across all known
    series. Fail-closed per series: a bad/renamed series ticker just
    yields zero markets for that series instead of aborting the scan."""
    markets: list[dict[str, Any]] = []
    for series in CRYPTO_15M_SERIES_TICKERS:
        try:
            found = api.get_markets(status="open", series_ticker=series, max_pages=2)
            markets.extend(found)
        except Exception:
            continue
    return markets


def _trade_to_row(ticker: str, event_ticker: str | None, trade: dict[str, Any], fetched_at) -> dict[str, Any] | None:
    trade_id = trade.get("trade_id")
    created_time = trade.get("created_time")
    if not trade_id or not created_time:
        return None
    try:
        created_dt = datetime.fromisoformat(created_time.replace("Z", "+00:00"))
    except ValueError:
        return None

    def _to_float(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    return {
        "created_at": fetched_at,
        "updated_at": fetched_at,
        "trade_id": trade_id,
        "ticker": trade.get("ticker") or ticker,
        "event_ticker": event_ticker,
        "created_time": created_dt,
        "yes_price": _to_float(trade.get("yes_price_dollars")),
        "no_price": _to_float(trade.get("no_price_dollars")),
        "count": _to_float(trade.get("count_fp")),
        "taker_side": trade.get("taker_side"),
        "fetched_at": fetched_at,
    }


def collect_real_ticks(api: KalshiAPI | None = None) -> dict[str, int]:
    """Fetches real recent trades for every currently-open crypto_15m
    market and persists new rows (deduped on trade_id) into
    KalshiMarketTick. Returns {"markets_scanned", "trades_seen",
    "rows_inserted"} for observability -- never raises, a total failure
    (e.g. Kalshi unreachable) just yields all-zero counts."""
    stats = {"markets_scanned": 0, "trades_seen": 0, "rows_inserted": 0}
    if api is None:
        api = KalshiAPI().initialize()

    try:
        init_db_historical()
    except Exception:
        return stats

    try:
        markets = _open_crypto_15m_tickers(api)
    except Exception:
        markets = []

    fetched_at = _now()
    for market in markets:
        ticker = market.get("ticker")
        if not ticker:
            continue
        event_ticker = (market.get("raw") or {}).get("event_ticker")
        stats["markets_scanned"] += 1
        try:
            trades = api.get_trades(ticker=ticker, limit=200, max_pages=3)
        except Exception:
            continue

        stats["trades_seen"] += len(trades)
        for trade in trades:
            row = _trade_to_row(ticker, event_ticker, trade, fetched_at)
            if row is None:
                continue
            try:
                exists = KalshiMarketTick.select().where(KalshiMarketTick.trade_id == row["trade_id"]).exists()
                if exists:
                    continue
                KalshiMarketTick.create(**row)
                stats["rows_inserted"] += 1
            except Exception:
                continue

    return stats
