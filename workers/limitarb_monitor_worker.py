"""
Exit-recommendation monitor worker for LimitArb signals (see
strategies/limit_arb.py and database/accounting/limitarbsignal.py for
the two-leg design: a MARKET leg executed now, a LIMIT leg placed at a
more favorable price that may take time -- or forever -- to fill).

HARD RED LINE -- READ FIRST
----------------------------
Kairos has NO real order execution anywhere (no auth/signing wired to
any platform). This worker NEVER places, modifies, or cancels a real
order. Everything below is monitoring/RECOMMENDATION logic only -- it
re-fetches the same market, evaluates 3 exit rules, and writes a
recommendation (`monitoring_status='exit_recommended'`, `exit_reason`,
`exit_locked_profit_pct`) to the signal row. A human decides whether to
act on it manually, exactly like every other part of this system (see
memory kairos-no-real-execution-user-trades-manually).

THE 3 EXIT RULES (evaluated in this order every cycle -- first one that
applies wins; a signal is only closed once)
------------------------------------------------------------------------
A) TIMEOUT: every ~30s cycle where the limit leg would NOT yet be
   marketable (current ask on `limit_leg_side` > `limit_leg_price`)
   counts as one "attempt" (`attempt_count`). After 3 such attempts in a
   row, recommend closing NOW with the market leg's current small
   profit rather than waiting indefinitely. If a cycle finds the limit
   leg WOULD be marketable (current price <= limit_leg_price), that is
   NOT a rule-A failure -- it's a valid close via 'limit_filled'
   (`monitoring_status` goes straight to 'closed', not
   'exit_recommended' -- see record_exit_recommendation in writer.py),
   and `attempt_count` is irrelevant at that point.

B) TRAILING STOP NEAR THE PINCH: tracks `best_limit_gap`, the minimum
   observed |current_limit_side_price - limit_leg_price| across all
   cycles for this signal. If that historical minimum ever got to
   <=0.05 (got close to triggering the limit) but the CURRENT gap is
   now larger than that historical minimum (price reversed away again),
   recommend closing with a trailing stop that locks in >=1% profit
   over the market leg's entry price.

C) TIME DECAY: if neither A nor B fired this cycle, and the fraction of
   monitoring-window time remaining is <=25% -- computed as
   (market_close_time - now) / (market_close_time - signal.created_at),
   using `market_close_time` (snapshotted at signal-creation time from
   the Kalshi market's own `close_time`, proxy for event end) and the
   signal's own `created_at` as the start of the monitoring window --
   recommend closing/selling the market leg NOW at whatever price
   applies, even if that locks in a loss (this rule's
   exit_locked_profit_pct CAN be negative, unlike A/B which only fire
   on a real/near-real gain). Expected to fire in a MINORITY of cases:
   under normal conditions rule A (3 failed attempts) or rule B
   (reversal near the pinch) should resolve most signals well before
   75% of the window elapses.

exit_locked_profit_pct FORMULA -- DESIGN DECISION
--------------------------------------------------
The only market-leg price this system has a reliable before/after pair
for is the FAVORITE side snapshot (`fav_price_at_signal` +
`fav_side`) added 2026-07-21 alongside outcome_observer_worker.py --
NOT necessarily the same side as `market_leg` (case
'favorite_overvalued' puts the market leg on the DOG, not the
favorite). Rather than fabricate a number for a leg we don't have a
clean entry-price snapshot for, this worker computes
exit_locked_profit_pct ONLY when the market leg's entry price is
recoverable from existing columns:
  - case 'favorite_undervalued': market_leg side == fav_side, so
    entry price == fav_price_at_signal. Directly usable.
  - case 'favorite_overvalued': market_leg side == dog_side, whose
    entry price was NEVER snapshotted (only fav_price_at_signal exists,
    not a dog-side snapshot) -- exit_locked_profit_pct is left as None
    in this case, on purpose, rather than guessed from
    `1 - fav_price_at_signal` (that's a same-instant complementary
    identity, not the same thing as the ACTUAL dog ask the market leg
    would have filled at, which can differ by the spread). This is a
    known limitation, not a bug -- documented explicitly here per
    project convention (never fabricate a number).
When computable: exit_locked_profit_pct =
    (current_market_leg_price - market_leg_entry_price) / market_leg_entry_price
(rounded to 4 decimals). This is a PRICE return on the market leg only
-- it does NOT net the limit leg's cost/fees (that number belongs to
`return_opportunity`/`price_adjustment`, computed at signal time in
strategies/limit_arb.py, not here). Documented as a simplification: this
worker's job is "should you abandon the plan", not a full re-run of the
two-leg profit model.

LOOP MODE
---------
Same pattern as outcome_observer_worker.py: if
`LIMITARB_MONITOR_INTERVAL_SECONDS` is set, loops forever (recommended
30s per the design brief); otherwise runs once and exits.

FAIL-CLOSED
-----------
Per-signal try/except -- one signal's fetch/parse failure never stops
the batch. A signal that fails is simply retried next cycle (still
'open').
"""

import os
import time
from datetime import datetime

import requests

from APIs.kalshi import KalshiAPI
from database.accounting.writer import get_open_signals, record_monitoring_check, record_exit_recommendation

ATTEMPT_LIMIT = 3
TRAILING_STOP_GAP_THRESHOLD = 0.05
TRAILING_STOP_MIN_PROFIT_PCT = 0.01
TIME_DECAY_FRACTION_REMAINING = 0.25


def _current_side_price(market: dict, side: str) -> float | None:
    if side == 'a':
        return market.get('current_a_price')
    if side == 'b':
        return market.get('current_b_price')
    return None


def _parse_dt(value):
    """Best-effort parse of a datetime that may already be a datetime
    object (Peewee round-trip) or an ISO-ish string (Kalshi's
    close_time, snapshotted verbatim by log_signal). Returns None if it
    can't be parsed -- never fabricates a fallback time."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
    return None


def _market_leg_entry_price(signal: dict) -> float | None:
    """See module docstring's exit_locked_profit_pct section -- only
    recoverable for case 'favorite_undervalued' (market leg == fav
    side, which was snapshotted). None otherwise, on purpose."""
    if signal.get('case') == 'favorite_undervalued' and signal.get('fav_price_at_signal') is not None:
        return signal['fav_price_at_signal']
    return None


def _market_leg_side(signal: dict) -> str | None:
    if signal.get('case') == 'favorite_undervalued':
        return signal.get('fav_side')
    if signal.get('case') == 'favorite_overvalued':
        # dog side = the side that isn't fav_side, 'a'/'b' only.
        fav = signal.get('fav_side')
        if fav == 'a':
            return 'b'
        if fav == 'b':
            return 'a'
    return None


def _locked_profit_pct(signal: dict, market: dict) -> float | None:
    entry = _market_leg_entry_price(signal)
    if entry is None or entry == 0:
        return None
    side = _market_leg_side(signal)
    current = _current_side_price(market, side) if side else None
    if current is None:
        return None
    return round((current - entry) / entry, 4)


def _monitor_one(api: KalshiAPI, signal: dict) -> str:
    """Evaluates the 3 rules for one open signal. Returns a short status
    string for the cycle summary. Never raises for expected conditions."""
    try:
        market = api.get_market(signal['market_id'])
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            record_exit_recommendation(signal_id=signal['id'], exit_reason='market_gone',
                                        exit_locked_profit_pct=None)
            return 'market_gone'
        raise

    if market is None:
        record_exit_recommendation(signal_id=signal['id'], exit_reason='market_gone',
                                    exit_locked_profit_pct=None)
        return 'market_gone'

    limit_side = signal.get('limit_leg_side')
    limit_price = signal.get('limit_leg_price')
    if limit_side is None or limit_price is None:
        # Legacy row (pre-snapshot columns) -- can't evaluate any rule
        # without knowing the limit leg. Leave it open rather than guess.
        return 'unmonitorable_legacy_row'

    current_limit_side_price = _current_side_price(market, limit_side)
    if current_limit_side_price is None:
        return 'no_price_data'

    # --- Rule A: timeout ---
    if current_limit_side_price <= limit_price:
        # Limit leg would be marketable now -- valid close, not a rule-A
        # failure. attempt_count is moot once this happens.
        record_exit_recommendation(signal_id=signal['id'], exit_reason='limit_filled',
                                    exit_locked_profit_pct=_locked_profit_pct(signal, market))
        return 'limit_filled'

    attempt_count = (signal.get('attempt_count') or 0) + 1
    gap = round(abs(current_limit_side_price - limit_price), 4)
    prior_best_gap = signal.get('best_limit_gap')
    best_limit_gap = gap if prior_best_gap is None else min(prior_best_gap, gap)

    if attempt_count >= ATTEMPT_LIMIT:
        record_exit_recommendation(signal_id=signal['id'], exit_reason='timeout',
                                    exit_locked_profit_pct=_locked_profit_pct(signal, market))
        return 'timeout'

    # --- Rule B: trailing stop near the pinch ---
    if prior_best_gap is not None and prior_best_gap <= TRAILING_STOP_GAP_THRESHOLD and gap > prior_best_gap:
        locked = _locked_profit_pct(signal, market)
        if locked is not None and locked >= TRAILING_STOP_MIN_PROFIT_PCT:
            record_exit_recommendation(signal_id=signal['id'], exit_reason='trailing_stop',
                                        exit_locked_profit_pct=locked)
            return 'trailing_stop'
        # Reversal detected but the >=1% profit floor isn't met (or isn't
        # computable) -- don't force a close that violates the stated
        # guarantee; fall through to rule C / keep monitoring.

    # --- Rule C: time decay ---
    close_time = _parse_dt(signal.get('market_close_time'))
    created_at = _parse_dt(signal.get('created_at'))
    if close_time is not None and created_at is not None:
        now = datetime.utcnow()
        window = (close_time - created_at).total_seconds()
        if window > 0:
            remaining_fraction = (close_time - now).total_seconds() / window
            if remaining_fraction <= TIME_DECAY_FRACTION_REMAINING:
                record_exit_recommendation(signal_id=signal['id'], exit_reason='time_decay',
                                            exit_locked_profit_pct=_locked_profit_pct(signal, market))
                return 'time_decay'

    # No rule fired -- just update the running tallies.
    record_monitoring_check(signal_id=signal['id'], attempt_count=attempt_count, best_limit_gap=best_limit_gap)
    return 'still_open'


def run_once() -> dict:
    api = KalshiAPI()
    open_signals = get_open_signals()

    counts: dict[str, int] = {}
    errors = 0
    for signal in open_signals:
        try:
            outcome = _monitor_one(api, signal)
            counts[outcome] = counts.get(outcome, 0) + 1
        except Exception as e:
            errors += 1
            print(f"[limitarb_monitor] signal_id={signal.get('id')} market_id={signal.get('market_id')} "
                  f"error: {e}")

    result = {'open': len(open_signals), 'errors': errors, **counts}
    print(f"[limitarb_monitor] {result}")
    return result


if __name__ == "__main__":
    _interval_s = os.getenv("LIMITARB_MONITOR_INTERVAL_SECONDS")
    if _interval_s:
        interval = int(_interval_s)
        print(f"[limitarb_monitor] loop mode, interval={interval}s")
        while True:
            try:
                run_once()
            except Exception as e:
                print(f"[limitarb_monitor] iteration error: {e}")
            time.sleep(interval)
    else:
        run_once()
