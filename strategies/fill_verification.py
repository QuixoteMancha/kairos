"""
Retrospective fill-verification for the LIMIT leg of a LimitArb signal
(see database/accounting/fillverification.py for the table this feeds
and workers/fill_verification_worker.py for the worker that calls this).

WHY A SEPARATE MECHANISM FROM limitarb_monitor_worker.py
-----------------------------------------------------------
`workers/limitarb_monitor_worker.py` runs every ~30s WHILE a signal is
still open and takes a live SNAPSHOT of the current ask price to guess
whether the limit leg "would be marketable". That is fast but lossy: a
price can cross the limit and revert between two 30s checks, and a
snapshot of the current ASK is not proof anything actually TRADED at
that price. This module runs ONCE, AFTER the event has settled, against
Kalshi's real trade tape (`outer_apis/kalshi.py:get_trades`,
`GET /markets/trades`, real executed trades) -- a strictly more honest
(but still imperfect) signal.

DOCUMENTED SIMPLIFICATIONS (all of them -- read before trusting a
verification result)
----------------------------------------------------------------------
1. Kalshi's public API exposes executed TRADES, not historical order-book
   depth. There is no way to know, after the fact, exactly how many
   contracts were resting ahead of a hypothetical limit order at our
   price. `queue_ahead_estimate` is a PROXY: the average trade size on
   the same ticker BEFORE the signal was created. This is used as a
   stand-in for "how much size typically trades in one clip on this
   market", not a real measurement of queued order size.
2. When there is NO trade history at all before the signal
   (`queue_ahead_estimate` can't be computed from data), we default it
   to 0.0 rather than leaving it unknown. This is a deliberately
   OPTIMISTIC assumption (it assumes nothing was queued ahead of us) and
   can therefore OVER-ESTIMATE how likely our hypothetical order was to
   fill. Documented here explicitly so a downstream reader of
   `limit_filled_verified=True` knows this bias exists.
3. `our_order_contracts` depends on a PaperTrade existing for the signal
   (position_size_usd / limit_leg_price). If no PaperTrade was opened
   (e.g. the position never cleared MIN_POSITION_USD), the classification
   cannot proceed past "did the price even touch" -- see the 'no
   computable' case below, never fabricated.
4. Classification is a simple volume-threshold heuristic (documented in
   compute_fill_verification's docstring below), not a queue-position
   simulation against a real limit order book. It assumes trades consume
   the queue in the order recorded, which Kalshi's trade tape does not
   explicitly guarantee (trades are timestamped, but this module doesn't
   attempt to reconstruct exact intra-second ordering).

Fail-closed throughout: every field that can't be honestly computed is
left None/False with a documented reason, never a fabricated value.
"""

from datetime import datetime, timezone

from outer_apis.kalshi.client import KalshiAPI

# How far back (in pages of up to `limit` trades each) to look for
# pre-signal trade history when estimating queue_ahead_estimate. Kept
# small on purpose -- this is a rough proxy for "typical recent trade
# size on this ticker", not a full trade-history reconstruction (which
# would also be slow and mostly irrelevant this far back).
QUEUE_LOOKBACK_MAX_PAGES = 2


def _to_naive_utc(value: datetime) -> datetime:
    """Same normalization rule used across the project (see
    workers/limitarb_monitor_worker.py:_parse_dt) -- naive-vs-aware
    datetime subtraction/comparison has broken this codebase twice
    already this session. Always normalize to naive UTC before using a
    datetime as a unix timestamp or comparing it to another one."""
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _parse_trade_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        return None
    return _to_naive_utc(parsed)


def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compute_fill_verification(api: KalshiAPI, *, limit_leg_ticker: str, limit_leg_price: float,
                               signal_created_at: datetime, settlement_ts: datetime | None,
                               our_order_contracts: float | None) -> dict:
    """Computes a retrospective fill-verification result for one signal's
    limit leg using Kalshi's real trade tape. See module docstring for
    all documented simplifications/biases -- this is a proxy, not a real
    fill confirmation (Kairos has no real order execution, hard red
    line).

    Returns a dict with exactly the fields FillVerification needs:
    price_touched, post_touch_volume, queue_ahead_estimate,
    limit_filled_verified, fail_reason.

    CLASSIFICATION (in order):
      not price_touched                                       -> filled=False, reason='no_price_touch'
      price_touched and our_order_contracts is None            -> filled=False, reason=None (not computable)
      price_touched and post_touch_volume < our_order_contracts
        -> filled=False, reason='insufficient_volume'
      price_touched and our_order_contracts <= post_touch_volume
        < (our_order_contracts + queue_ahead_estimate)
        -> filled=False, reason='queue_position'
      price_touched and post_touch_volume >=
        (our_order_contracts + queue_ahead_estimate)
        -> filled=True, reason=None
    """
    signal_created_at = _to_naive_utc(signal_created_at)
    min_ts = int(signal_created_at.timestamp())
    if settlement_ts is not None:
        max_ts = int(_to_naive_utc(settlement_ts).timestamp())
    else:
        # Documented fallback: caller (worker) should not reach this case
        # until the event is confirmed settled -- see
        # workers/fill_verification_worker.py step 2. Kept here only as a
        # safety net so this function never raises for a missing
        # settlement_ts; the resulting window may simply be incomplete
        # (still-open event).
        max_ts = int(datetime.utcnow().timestamp())

    post_trades = api.get_trades(ticker=limit_leg_ticker, min_ts=min_ts, max_ts=max_ts)

    qualifying_trades = []
    for t in post_trades:
        t_dt = _parse_trade_dt(t.get('created_time'))
        t_price = _to_float(t.get('yes_price_dollars'))
        if t_dt is None or t_price is None:
            continue
        if t_dt >= signal_created_at and t_price <= limit_leg_price:
            qualifying_trades.append(t)

    price_touched = len(qualifying_trades) > 0
    post_touch_volume = None
    if price_touched:
        post_touch_volume = sum(
            (_to_float(t.get('count_fp')) or 0.0) for t in qualifying_trades
        )

    # queue_ahead_estimate: average trade size BEFORE the signal, on the
    # same ticker. get_trades(max_ts=...) is exclusive-enough for our
    # purposes here since we filter by strict `<` below regardless of API
    # boundary behavior.
    pre_trades = api.get_trades(ticker=limit_leg_ticker, max_ts=min_ts,
                                 max_pages=QUEUE_LOOKBACK_MAX_PAGES)
    pre_sizes = []
    for t in pre_trades:
        t_dt = _parse_trade_dt(t.get('created_time'))
        if t_dt is None or t_dt >= signal_created_at:
            continue
        size = _to_float(t.get('count_fp'))
        if size is not None:
            pre_sizes.append(size)
    queue_ahead_estimate = (sum(pre_sizes) / len(pre_sizes)) if pre_sizes else 0.0

    if not price_touched:
        return {
            'price_touched': False,
            'post_touch_volume': None,
            'queue_ahead_estimate': queue_ahead_estimate,
            'limit_filled_verified': False,
            'fail_reason': 'no_price_touch',
        }

    if our_order_contracts is None:
        # Price touched, but we can't classify volume/queue without
        # knowing our hypothetical order size -- not computable, not a
        # fabricated classification.
        return {
            'price_touched': True,
            'post_touch_volume': post_touch_volume,
            'queue_ahead_estimate': queue_ahead_estimate,
            'limit_filled_verified': False,
            'fail_reason': None,
        }

    if post_touch_volume < our_order_contracts:
        fail_reason = 'insufficient_volume'
        filled = False
    elif post_touch_volume < (our_order_contracts + queue_ahead_estimate):
        fail_reason = 'queue_position'
        filled = False
    else:
        fail_reason = None
        filled = True

    return {
        'price_touched': True,
        'post_touch_volume': post_touch_volume,
        'queue_ahead_estimate': queue_ahead_estimate,
        'limit_filled_verified': filled,
        'fail_reason': fail_reason,
    }
