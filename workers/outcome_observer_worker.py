"""
Outcome observer worker for LimitArb signals (see strategies/limit_arb.py
and database/accounting/limitarbsignal.py for the full design context —
W_EDGE/W_VOLATILITY are unfitted initial constants, and this worker is
the missing piece that eventually makes recalibrating them against real
data possible: it fills in the outcome columns that are ALWAYS NULL
today. Recalibration itself is future work, NOT done here — this is only
the observation loop).

WHAT IT DOES
------------
For each logged LimitArbSignal that hasn't been observed yet
(`outcome_observed_at IS NULL`) and falls inside the observation window
below, re-fetch that SAME market from Kalshi (`market_id` stored on the
signal IS the ticker Kalshi's `get_market` needs directly --
outer_apis/kalshi.py's `_normalize_market` sets `"id": raw.get("ticker")`, so
no translation step is needed) and record what actually happened:

  - `observed_price_move`: |current fav price - fav_price_at_signal|,
    using the `fav_side`/`fav_price_at_signal` snapshot columns added
    2026-07-21 alongside this worker (see limitarbsignal.py). Rows
    logged BEFORE that change don't have this snapshot -- for those,
    `observed_price_move` stays NULL (never fabricated) and
    `limit_leg_filled` is recorded as `'unknown_legacy_row'`.

  - `limit_leg_filled`: this project has NO real order execution (hard
    red line, see memory kairos-no-real-execution-user-trades-manually)
    so there is no fills-webhook or broker state to check. The best
    honest proxy available is: would the limit order, as planned
    (`limit_leg_side`/`limit_leg_price`), have been marketable by now?
    i.e. did the CURRENT ask price on that side drop to or below the
    planned limit price. That's an inference, not a confirmed fill --
    documented as such rather than pretended to be real fill data.
    'market_gone' is used when Kalshi no longer returns the ticker at
    all (settled/removed) -- a real, valid outcome, not a skip.

OBSERVATION WINDOW -- WHY THESE BOUNDS
---------------------------------------
Kalshi markets range from crypto_15m (resolves in ~15 minutes) to
sports/political markets (days to weeks). There is no single delay that
is "correct" for all of them -- this worker uses one fixed window as a
documented approximation, not a per-market-category deadline (that would
need category-aware scheduling, out of scope here):

  - MIN_AGE = 2 hours: gives the market enough time to move beyond
    normal bid/ask noise so `observed_price_move` reflects a real drift,
    not a random tick. Short enough that even crypto_15m signals (which
    resolve in ~15 min, long before 2h) will already show their FINAL
    settled price by observation time -- for those, "observed" really
    means "post-resolution", which is fine: the price is real, not a
    snapshot mid-flight.
  - MAX_AGE = 30 days: a soft cap. Signals older than this are left
    unobserved rather than fetched -- by 30 days almost every market in
    this system has resolved one way or another, and re-checking
    arbitrarily old rows forever (e.g. if the worker was down for a
    while) isn't worth an API call per stale row. This means a small
    number of very old, never-observed rows can accumulate if the
    worker is offline for a long stretch -- acceptable given this table
    is still low-volume (documented tradeoff, not a bug).

FAIL-CLOSED / IDEMPOTENT
-------------------------
- Per-row try/except: one signal's fetch/parse failure never stops the
  batch (same pattern as strategies/limit_arb.py's log_signal call and
  kairos.py's scan loop).
- Only rows with `outcome_observed_at IS NULL` are selected, and every
  successful or handled row gets `outcome_observed_at` set -- so a row
  is never re-observed twice, and a genuinely un-observable row (legacy,
  gone market) is still marked "observed" (with an honest reason) rather
  than retried forever.
- Never fabricates a price: if the market can't be fetched or the
  snapshot columns are missing, `observed_price_move` stays NULL.

LOOP MODE
---------
Same pattern as kairos.py: if `OUTCOME_OBSERVER_INTERVAL_SECONDS` is set,
loops forever with per-iteration try/except; otherwise runs once and
exits.
"""

import os
import time

import requests

from outer_apis.kalshi.client import KalshiAPI
from database.accounting.writer import get_unobserved_signals, record_outcome

MIN_AGE_SECONDS = 2 * 3600        # 2 hours
MAX_AGE_SECONDS = 30 * 24 * 3600  # 30 days


def _current_side_price(market: dict, side: str) -> float | None:
    if side == 'a':
        return market.get('current_a_price')
    if side == 'b':
        return market.get('current_b_price')
    return None


def _fetch_leg_price(api: KalshiAPI, ticker: str, side: str, same_ticket: bool) -> float | None:
    """Same helper as workers/limitarb_monitor_worker.py -- fetches one
    leg's current price by its OWN real ticker, falling back to
    KalshiAPI.get_market_any_status()'s real settlement 'result' (as a
    TERMINAL 0.0/1.0 price) when the direct ticker lookup 404s (purged
    after settling). Returns None only if genuinely nothing is known."""
    try:
        market = api.get_market(ticker)
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            market = None
        else:
            raise

    if market is not None:
        return _current_side_price(market, side) if same_ticket else market.get('current_a_price')

    any_status = api.get_market_any_status(ticker)
    if any_status is not None:
        result = (any_status.get('raw') or {}).get('result')
        if result in ('yes', 'no'):
            return 1.0 if result == 'yes' else 0.0
    return None


def _ticker_for_side(signal: dict, side: str) -> str | None:
    """fav_side always equals either market_leg_side or limit_leg_side
    (see strategies/limit_arb.py's _build_order_plan: case
    'favorite_undervalued' -> market_leg==fav_side; case
    'favorite_overvalued' -> limit_leg==fav_side) -- so its real ticker
    can always be recovered from the two leg-ticker columns without a
    dedicated fav-side-ticker column."""
    if side is not None and side == signal.get('market_leg_side'):
        return signal.get('market_leg_ticker')
    if side is not None and side == signal.get('limit_leg_side'):
        return signal.get('limit_leg_ticker')
    return None


def _observe_one(api: KalshiAPI, signal: dict) -> tuple[float | None, str]:
    """Returns (observed_price_move, limit_leg_filled) for one signal.
    Never raises for expected conditions (missing snapshot, market gone)
    -- those are valid, honestly-labeled outcomes, not errors.

    Added 2026-07-23: when market_leg_ticker/limit_leg_ticker are
    populated (real per-leg Kalshi tickers), each leg is fetched
    INDEPENDENTLY by its own ticker instead of `signal['market_id']` --
    fixes the cross-ticket bug (tenis/team sports) where market_id is a
    synthetic, never-fetchable event_ticker, see module docstring /
    workers/limitarb_monitor_worker.py. Legacy rows (both ticker columns
    None) fall back to the EXACT old market_id-based fetch, unchanged."""
    market_leg_ticker = signal.get('market_leg_ticker')
    limit_leg_ticker = signal.get('limit_leg_ticker')

    has_snapshot = (signal.get('fav_side') is not None
                     and signal.get('fav_price_at_signal') is not None
                     and signal.get('limit_leg_side') is not None
                     and signal.get('limit_leg_price') is not None)

    if not has_snapshot:
        # Pre-2026-07-21 row, logged before the snapshot columns
        # existed -- honestly can't reconstruct fav/limit prices.
        return None, 'unknown_legacy_row'

    if market_leg_ticker and limit_leg_ticker:
        same_ticket = (market_leg_ticker == limit_leg_ticker)
        fav_ticker = _ticker_for_side(signal, signal['fav_side'])
        limit_ticker = _ticker_for_side(signal, signal['limit_leg_side'])

        current_fav_price = (_fetch_leg_price(api, fav_ticker, signal['fav_side'], same_ticket)
                              if fav_ticker else None)
        current_limit_side_price = (_fetch_leg_price(api, limit_ticker, signal['limit_leg_side'], same_ticket)
                                     if limit_ticker else None)

        if current_fav_price is None and current_limit_side_price is None:
            # Neither leg resolvable via live fetch nor real settlement --
            # genuinely gone (never existed, or a transient real error).
            return None, 'market_gone'
    else:
        # Legacy row (pre-2026-07-23, no per-leg ticker columns) -- exact
        # old behavior: fetch by market_id.
        try:
            market = api.get_market(signal['market_id'])
        except requests.exceptions.HTTPError as e:
            # Kalshi returns 404 for a ticker that no longer exists (settled
            # and purged, or malformed) -- outer_apis/kalshi.py's get_market()
            # doesn't itself translate that into None (it only returns None
            # when the response body has no "market" key), so a 404 raises
            # here. That's still a real, honest outcome for this worker's
            # purposes, same as a body-shaped "not found".
            if e.response is not None and e.response.status_code == 404:
                return None, 'market_gone'
            raise

        if market is None:
            # Settled/removed -- a real outcome, not a failure to hide.
            return None, 'market_gone'

        current_fav_price = _current_side_price(market, signal['fav_side'])
        current_limit_side_price = _current_side_price(market, signal['limit_leg_side'])

    if current_fav_price is None:
        return None, 'unknown_legacy_row'

    observed_price_move = round(abs(current_fav_price - signal['fav_price_at_signal']), 4)

    if current_limit_side_price is None:
        limit_leg_filled = 'unknown_legacy_row'
    else:
        # The limit leg was placed BELOW the price at signal time
        # (buy-limit) -- it would be marketable/filled once the ask on
        # that side drops to or below the planned limit price. A TERMINAL
        # price (0.0/1.0, reconstructed from a real settlement 'result')
        # is a perfectly valid input to this same comparison.
        limit_leg_filled = 'yes' if current_limit_side_price <= signal['limit_leg_price'] else 'no'

    return observed_price_move, limit_leg_filled


def run_once() -> dict:
    api = KalshiAPI()
    eligible = get_unobserved_signals(min_age_seconds=MIN_AGE_SECONDS, max_age_seconds=MAX_AGE_SECONDS)

    observed = 0
    errors = 0
    for signal in eligible:
        try:
            price_move, filled = _observe_one(api, signal)
            record_outcome(signal_id=signal['id'], observed_price_move=price_move,
                            limit_leg_filled=filled)
            observed += 1
        except Exception as e:
            # Fail-closed per row: never let one signal's failure stop
            # the batch. The row stays unobserved and will be retried
            # next run (still inside the age window, presumably).
            errors += 1
            print(f"[outcome_observer] signal_id={signal.get('id')} market_id={signal.get('market_id')} "
                  f"error: {e}")

    result = {'eligible': len(eligible), 'observed': observed, 'errors': errors}
    print(f"[outcome_observer] {result}")
    return result


if __name__ == "__main__":
    _interval_s = os.getenv("OUTCOME_OBSERVER_INTERVAL_SECONDS")
    if _interval_s:
        interval = int(_interval_s)
        print(f"[outcome_observer] loop mode, interval={interval}s")
        while True:
            try:
                run_once()
            except Exception as e:
                print(f"[outcome_observer] iteration error: {e}")
            time.sleep(interval)
    else:
        run_once()
