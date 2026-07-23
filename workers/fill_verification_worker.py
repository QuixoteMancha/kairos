"""
Retrospective fill-verification worker for LimitArb signals (see
strategies/fill_verification.py for the algorithm and all documented
approximations, and database/accounting/fillverification.py for the
table this writes).

HARD RED LINE -- READ FIRST: Kairos has NO real order execution anywhere.
This worker only reads PUBLIC Kalshi data (real executed trades via
`APIs/kalshi.py:get_trades`) and writes a retrospective ANALYSIS row --
it never places, modifies, or cancels anything.

WHAT IT DOES, EACH CYCLE
-------------------------
1. Selects candidate LimitArbSignal rows: already resolved by the live
   monitor (`monitoring_status` in ('closed', 'exit_recommended')),
   with a real `limit_leg_ticker` (legacy rows without one aren't
   verifiable, skipped), and without an existing FillVerification row
   (write-once per signal) -- see
   database/accounting/writer.py:get_unverified_resolved_signals.
2. For each candidate, confirms the underlying EVENT has actually
   settled: calls `api.get_market_any_status(limit_leg_ticker)` and
   checks `raw.get('result')` is in ('yes', 'no'). If not settled yet,
   SKIPS it this cycle (not an error, just not ready -- retried next
   cycle). `monitoring_status` resolved != event settled: the live
   monitor can close a signal (timeout/trailing_stop/time_decay) well
   before the underlying market actually finishes.
3. If settled, extracts `settlement_ts` from the raw payload (parsed
   tzinfo-safe, normalized to naive UTC -- same rule enforced project-
   wide, see strategies/fill_verification.py:_to_naive_utc) and calls
   strategies.fill_verification.compute_fill_verification.
4. Looks up the associated PaperTrade (by signal_id) to compute
   our_order_contracts = position_size_usd / limit_leg_price. None if no
   PaperTrade exists for this signal (never fabricated).
5. Persists the result via
   database/accounting/writer.py:record_fill_verification.

FAIL-CLOSED
-----------
Per-signal try/except -- one signal's failure never stops the batch or
crashes the cycle; it's simply retried next cycle (still no
FillVerification row for it).

LOOP MODE
---------
Same pattern as the other 4 workers: if
`FILL_VERIFICATION_INTERVAL_SECONDS` is set, loops forever; otherwise
runs once and exits. Default suggested interval is 600s (10 min) -- this
is retrospective analysis, not time-critical live monitoring like
limitarb_monitor_worker.py's 30s.
"""

import os
import time
from datetime import datetime, timezone

from APIs.kalshi import KalshiAPI
from database.accounting.writer import get_unverified_resolved_signals, record_fill_verification
from database.accounting.papertrade import PaperTrade
from database.db import db, init_db
from strategies.fill_verification import compute_fill_verification


def _parse_settlement_ts(raw_value) -> "datetime | None":
    if not raw_value:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw_value).replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _our_order_contracts(signal: dict) -> float | None:
    """Looks up the PaperTrade associated with this signal (if any) to
    derive our_order_contracts = position_size_usd / limit_leg_price.
    None if no PaperTrade exists, or limit_leg_price is missing/zero --
    never fabricated."""
    limit_leg_price = signal.get('limit_leg_price')
    if not limit_leg_price:
        return None
    init_db()
    with db.connection_context():
        trade = (PaperTrade
                 .select()
                 .where(PaperTrade.signal_id == signal['id'])
                 .order_by(PaperTrade.opened_at.desc())
                 .first())
    if trade is None:
        return None
    return trade.position_size_usd / limit_leg_price


def _verify_one(api: KalshiAPI, signal: dict) -> str:
    limit_leg_ticker = signal.get('limit_leg_ticker')
    limit_leg_price = signal.get('limit_leg_price')
    created_at = signal.get('created_at')
    if not limit_leg_ticker or limit_leg_price is None or not created_at:
        return 'unverifiable_missing_fields'

    any_status = api.get_market_any_status(limit_leg_ticker)
    if any_status is None:
        return 'market_unknown'
    raw = any_status.get('raw') or {}
    result = raw.get('result')
    if result not in ('yes', 'no'):
        return 'not_settled_yet'

    settlement_ts = _parse_settlement_ts(raw.get('settlement_ts'))
    signal_created_at = datetime.fromisoformat(created_at) if isinstance(created_at, str) else created_at

    our_order_contracts = _our_order_contracts(signal)

    result_dict = compute_fill_verification(
        api,
        limit_leg_ticker=limit_leg_ticker,
        limit_leg_price=limit_leg_price,
        signal_created_at=signal_created_at,
        settlement_ts=settlement_ts,
        our_order_contracts=our_order_contracts,
    )

    record_fill_verification(
        signal_id=signal['id'],
        market_id=signal['market_id'],
        price_touched=result_dict['price_touched'],
        post_touch_volume=result_dict['post_touch_volume'],
        queue_ahead_estimate=result_dict['queue_ahead_estimate'],
        our_order_contracts=our_order_contracts,
        limit_filled_verified=result_dict['limit_filled_verified'],
        fail_reason=result_dict['fail_reason'],
    )
    return 'verified'


def run_once() -> dict:
    api = KalshiAPI()
    candidates = get_unverified_resolved_signals()

    counts: dict[str, int] = {}
    errors = 0
    for signal in candidates:
        try:
            outcome = _verify_one(api, signal)
            counts[outcome] = counts.get(outcome, 0) + 1
        except Exception as e:
            errors += 1
            print(f"[fill_verification] signal_id={signal.get('id')} market_id={signal.get('market_id')} "
                  f"error: {e}")

    result = {'candidates': len(candidates), 'errors': errors, **counts}
    print(f"[fill_verification] {result}")
    return result


if __name__ == "__main__":
    _interval_s = os.getenv("FILL_VERIFICATION_INTERVAL_SECONDS")
    if _interval_s:
        interval = int(_interval_s)
        print(f"[fill_verification] loop mode, interval={interval}s")
        while True:
            try:
                run_once()
            except Exception as e:
                print(f"[fill_verification] iteration error: {e}")
            time.sleep(interval)
    else:
        run_once()
