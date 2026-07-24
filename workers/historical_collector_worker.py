"""
Worker that keeps `KalshiMarketTick` and `CryptoPriceSnapshot`
(database/historical/market_data/) freshly populated going forward.

Both tables were rescued from an old system with real historical rows but
had NOTHING writing new rows to them (see database/db.py's
`init_db_historical` docstring). This worker fills that gap with two
independent, fail-closed collectors, run each cycle:

  - Part A: real per-trade Kalshi ticks for currently-open crypto_15m
    markets (outer_apis/kalshi_ticks_collector.py, KalshiAPI.get_trades).
  - Part B: real underlying OHLCV from Binance's public klines endpoint
    (outer_apis/crypto_prices.py).

Neither part ever fabricates data -- a fetch/parse failure for one
market/symbol just yields zero rows for it and moves on, never blocks the
other part or the other markets/symbols within the same part.

Same loop pattern as workers/outcome_observer_worker.py: if
`HISTORICAL_COLLECTOR_INTERVAL_SECONDS` is set, runs forever with a
per-iteration try/except so a bad cycle never kills the process; if
unset, runs once (useful for cron/manual verification).
"""

import os
import time

from outer_apis.crypto_prices import collect_real_prices
from outer_apis.kalshi_ticks_collector import collect_real_ticks


def run_once() -> dict:
    ticks_stats = {}
    try:
        ticks_stats = collect_real_ticks()
    except Exception as e:
        print(f"[historical_collector] kalshi ticks error: {e}")

    prices_stats = {}
    try:
        prices_stats = collect_real_prices()
    except Exception as e:
        print(f"[historical_collector] binance prices error: {e}")

    result = {"kalshi_ticks": ticks_stats, "crypto_prices": prices_stats}
    print(f"[historical_collector] {result}")
    return result


if __name__ == "__main__":
    _interval_s = os.getenv("HISTORICAL_COLLECTOR_INTERVAL_SECONDS")
    if _interval_s:
        interval = int(_interval_s)
        print(f"[historical_collector] loop mode, interval={interval}s")
        while True:
            try:
                run_once()
            except Exception as e:
                print(f"[historical_collector] iteration error: {e}")
            time.sleep(interval)
    else:
        run_once()
