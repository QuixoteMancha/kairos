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
from datetime import datetime, timezone

import requests

from APIs.kalshi import KalshiAPI
from database.accounting.writer import get_open_signals, record_monitoring_check, record_exit_recommendation
from strategies.paper_trading import close_paper_trade, recalibrate_kelly_factor

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
    can't be parsed -- never fabricates a fallback time.

    BUG FOUND IN PROD 2026-07-23 (round 2): the original fix here only
    normalized tzinfo in the STRING-parsing branch, assuming the
    `isinstance(value, datetime)` branch (Peewee round-trip) always came
    back naive. Verified live against a real open signal in prod:
    `market_close_time` actually comes back from Peewee as a
    TIMEZONE-AWARE datetime (tzinfo=UTC) while `created_at` comes back
    as a naive ISO string -- the datetime-object branch returned it
    as-is, unnormalized, and Rule C's subtraction against the naive
    `created_at`/`datetime.utcnow()` raised the exact same
    "can't subtract offset-naive and offset-aware datetimes" error the
    first fix (e1a5593) was supposed to close. Both branches now
    normalize to naive UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
        # Normalize to naive UTC -- Kalshi's close_time parses tz-aware
        # (+00:00), but created_at (Peewee, default=datetime.utcnow) and
        # datetime.utcnow() below are naive. Mixing the two raises
        # TypeError on subtraction (verified: this silently broke Rule C
        # on every signal, always caught by the per-signal try/except in
        # run_once -- fixed here rather than left undiscovered).
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    return None


def _market_leg_entry_price(signal: dict) -> float | None:
    """Added 2026-07-23 (see limitarbsignal.py): market_leg_price_at_signal
    now covers BOTH cases directly (previously only 'favorite_undervalued'
    was computable via fav_price_at_signal -- 'favorite_overvalued' had no
    entry-price snapshot for the dog-side market leg at all). Rows logged
    before this column existed still return None here -- never fabricated
    via a same-instant complementary-price approximation."""
    return signal.get('market_leg_price_at_signal')


def _market_leg_side(signal: dict) -> str | None:
    """Same rationale as above -- market_leg_side now covers both cases
    directly. Legacy rows (pre-2026-07-23) fall back to the old
    fav_side-based inference for 'favorite_undervalued' only, since that
    one snapshot did already exist."""
    if signal.get('market_leg_side') is not None:
        return signal['market_leg_side']
    if signal.get('case') == 'favorite_undervalued':
        return signal.get('fav_side')
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


def _fetch_leg_price(api: KalshiAPI, ticker: str, side: str, same_ticket: bool) -> float | None:
    """Fetches the current price for one leg. `same_ticket` distinguishes
    the two shapes documented in the module docstring / limit_arb.py:
      - Cross-ticket (tenis/team sports): `ticker` is that leg's OWN real
        Kalshi ticker (never the synthetic event_ticker). Its own
        `current_a_price` (yes ask) IS the combined-market price for this
        leg regardless of which side ('a'/'b') it played in the pairing --
        see build_tennis_markets: `current_a_price/b_price` = each leg's
        own `current_a_price`, not a bid/ask pair of ONE market.
      - Single-ticket: `ticker` == the market itself, side selects
        current_a_price vs current_b_price of that one market as always.

    Fail-closed fallback: if `api.get_market(ticker)` 404s (purged after
    settling), tries `api.get_market_any_status(ticker)` -- if that
    returns a real settlement `result` ('yes'/'no'), reconstructs a
    TERMINAL price (1.0/0.0), NOT a live quote. Returns None only if
    genuinely nothing is known (never existed, or still unsettled but
    somehow 404ing on the direct lookup -- documented edge case)."""
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


def _close_paper_trade_safe(signal: dict, exit_reason: str, exit_locked_profit_pct: float | None,
                             limit_leg_price: float | None = None,
                             close_market_leg_price: float | None = None) -> None:
    """Mirrors a record_exit_recommendation() call onto the paper-trading
    ledger (see strategies/paper_trading.py) -- purely simulated money,
    never a real order. Own try/except: a paper-trading failure must
    never break real guardrail monitoring.

    category is always None here: get_open_signals()/_row_to_dict() in
    writer.py doesn't carry market['type'] on the signal dict (it's only
    available at signal-creation time in strategies/limit_arb.py, not
    persisted on LimitArbSignal). Consequence: for exit_reason
    'limit_filled', close_paper_trade can't deduct the limit leg's fee
    precisely here -- it falls back to its documented 0-fee approximation
    (conservative-optimistic, not fatal). Not fabricating a category."""
    try:
        close_paper_trade(signal_id=signal['id'], exit_reason=exit_reason,
                           exit_locked_profit_pct=exit_locked_profit_pct,
                           return_opportunity=signal.get('return_opportunity'),
                           limit_leg_price=limit_leg_price, category=None,
                           close_market_leg_price=close_market_leg_price)
    except Exception as e:
        print(f"[limitarb_monitor] paper-trade close failed signal_id={signal.get('id')} error: {e}")


def _monitor_one(api: KalshiAPI, signal: dict) -> str:
    """Evaluates the 3 rules for one open signal. Returns a short status
    string for the cycle summary. Never raises for expected conditions.

    Added 2026-07-23: for signals that have market_leg_ticker/
    limit_leg_ticker (real per-leg Kalshi tickers, see limitarbsignal.py
    and strategies/limit_arb.py), each leg is fetched INDEPENDENTLY via
    api.get_market(ticker) instead of api.get_market(signal['market_id']).
    This fixes the cross-ticket bug (tenis/team sports): market_id there
    is a synthetic event_ticker that 404s unconditionally, regardless of
    whether the match is still live or already settled -- see module
    docstring. Legacy rows (market_leg_ticker/limit_leg_ticker both None,
    pre-2026-07-23) fall back to the EXACT old behavior below, unchanged."""
    limit_side = signal.get('limit_leg_side')
    limit_price = signal.get('limit_leg_price')
    if limit_side is None or limit_price is None:
        # Legacy row (pre-snapshot columns) -- can't evaluate any rule
        # without knowing the limit leg. Leave it open rather than guess.
        return 'unmonitorable_legacy_row'

    market_leg_ticker = signal.get('market_leg_ticker')
    limit_leg_ticker = signal.get('limit_leg_ticker')
    market_leg_side = signal.get('market_leg_side')

    if market_leg_ticker and limit_leg_ticker:
        same_ticket = (market_leg_ticker == limit_leg_ticker)
        current_limit_side_price = _fetch_leg_price(api, limit_leg_ticker, limit_side, same_ticket)
        current_market_leg_price = (
            _fetch_leg_price(api, market_leg_ticker, market_leg_side, same_ticket)
            if market_leg_side is not None else None
        )

        if current_limit_side_price is None and current_market_leg_price is None:
            # Neither a live quote nor a real settlement result was
            # recoverable for either leg -- genuinely gone (never existed,
            # or a transient real error), same fallback as always.
            record_exit_recommendation(signal_id=signal['id'], exit_reason='market_gone',
                                        exit_locked_profit_pct=None)
            _close_paper_trade_safe(signal, 'market_gone', None)
            return 'market_gone'

        # Synthetic combined-market dict, reconstructed from the two
        # independent per-leg fetches -- feeds the SAME 3-rule logic below
        # (_current_side_price/_locked_profit_pct already operate on this
        # {'current_a_price', 'current_b_price'} shape) without rewriting
        # it. Values here may be TERMINAL prices (0.0/1.0) reconstructed
        # from a real Kalshi settlement 'result' rather than a live quote
        # -- valid inputs for the same comparisons either way.
        market: dict = {}
        market['current_a_price' if limit_side == 'a' else 'current_b_price'] = current_limit_side_price
        if market_leg_side is not None:
            market['current_a_price' if market_leg_side == 'a' else 'current_b_price'] = current_market_leg_price
    else:
        # Legacy row (pre-2026-07-23, no per-leg ticker columns) -- exact
        # old behavior: fetch by market_id (works for single-ticket
        # markets, always 404s for cross-ticket tennis/team-sports).
        try:
            market = api.get_market(signal['market_id'])
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                record_exit_recommendation(signal_id=signal['id'], exit_reason='market_gone',
                                            exit_locked_profit_pct=None)
                _close_paper_trade_safe(signal, 'market_gone', None)
                return 'market_gone'
            raise

        if market is None:
            record_exit_recommendation(signal_id=signal['id'], exit_reason='market_gone',
                                        exit_locked_profit_pct=None)
            _close_paper_trade_safe(signal, 'market_gone', None)
            return 'market_gone'

    current_limit_side_price = _current_side_price(market, limit_side)
    if current_limit_side_price is None:
        return 'no_price_data'

    close_market_leg_price = _current_side_price(market, _market_leg_side(signal))

    # --- Rule A: timeout ---
    if current_limit_side_price <= limit_price:
        # Limit leg would be marketable now -- valid close, not a rule-A
        # failure. attempt_count is moot once this happens.
        record_exit_recommendation(signal_id=signal['id'], exit_reason='limit_filled',
                                    exit_locked_profit_pct=_locked_profit_pct(signal, market))
        _close_paper_trade_safe(signal, 'limit_filled', _locked_profit_pct(signal, market),
                                 limit_leg_price=limit_price, close_market_leg_price=close_market_leg_price)
        return 'limit_filled'

    attempt_count = (signal.get('attempt_count') or 0) + 1
    gap = round(abs(current_limit_side_price - limit_price), 4)
    prior_best_gap = signal.get('best_limit_gap')
    best_limit_gap = gap if prior_best_gap is None else min(prior_best_gap, gap)

    if attempt_count >= ATTEMPT_LIMIT:
        record_exit_recommendation(signal_id=signal['id'], exit_reason='timeout',
                                    exit_locked_profit_pct=_locked_profit_pct(signal, market))
        _close_paper_trade_safe(signal, 'timeout', _locked_profit_pct(signal, market),
                                 close_market_leg_price=close_market_leg_price)
        return 'timeout'

    # --- Rule B: trailing stop near the pinch ---
    if prior_best_gap is not None and prior_best_gap <= TRAILING_STOP_GAP_THRESHOLD and gap > prior_best_gap:
        locked = _locked_profit_pct(signal, market)
        if locked is not None and locked >= TRAILING_STOP_MIN_PROFIT_PCT:
            record_exit_recommendation(signal_id=signal['id'], exit_reason='trailing_stop',
                                        exit_locked_profit_pct=locked)
            _close_paper_trade_safe(signal, 'trailing_stop', locked, close_market_leg_price=close_market_leg_price)
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
                locked = _locked_profit_pct(signal, market)
                record_exit_recommendation(signal_id=signal['id'], exit_reason='time_decay',
                                            exit_locked_profit_pct=locked)
                _close_paper_trade_safe(signal, 'time_decay', locked, close_market_leg_price=close_market_leg_price)
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

    # Recalibrate the Kelly proxy factor once per cycle, after all signals
    # in this cycle have been processed (and any paper trades closed
    # above). Own try/except -- a recalibration failure must never break
    # the real guardrail monitoring summary.
    try:
        recalibration = recalibrate_kelly_factor()
    except Exception as e:
        recalibration = {'recalibrated': False, 'error': str(e)}

    result = {'open': len(open_signals), 'errors': errors, **counts, 'kelly_recalibration': recalibration}
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
