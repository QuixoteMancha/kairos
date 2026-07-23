"""
Call surface for logging/reading LimitArb signals (see
database/accounting/limitarbsignal.py for the schema and why it exists —
this is the groundwork for eventually recalibrating the price_adjustment
regression weights against real outcomes, not a finished training loop).
"""

from datetime import datetime

from database.db import db, init_db
from database.accounting.limitarbsignal import LimitArbSignal
from database.accounting.fillverification import FillVerification


def log_signal(*, market_id: str, avg_volatility: float, raw_edge: float,
                price_adjustment: float, w_edge: float, w_volatility: float,
                case: str, return_opportunity: float,
                fav_side: str | None = None, fav_price_at_signal: float | None = None,
                limit_leg_side: str | None = None, limit_leg_price: float | None = None,
                market_close_time=None,
                market_leg_side: str | None = None, market_leg_price_at_signal: float | None = None,
                market_leg_ticker: str | None = None, limit_leg_ticker: str | None = None) -> int:
    """Persists one LimitArb signal, returns the new row's id (used by
    strategies/limit_arb.py to open a paper trade against it, see
    strategies/paper_trading.py). Fail-closed at the call site (see
    strategies/limit_arb.py, wrapped in try/except): a logging failure
    must never crash or block a scan.

    The fav_*/limit_leg_* snapshot params are optional (default None) so
    older callers keep working, but strategies/limit_arb.py now always
    passes them -- they're what workers/outcome_observer_worker.py needs
    to later diff against the real market price and know which side/price
    to check for a fill."""
    init_db()
    with db.connection_context():
        row = LimitArbSignal.create(
            market_id=market_id,
            case=case,
            avg_volatility=avg_volatility,
            raw_edge=raw_edge,
            price_adjustment=price_adjustment,
            w_edge=w_edge,
            w_volatility=w_volatility,
            return_opportunity=return_opportunity,
            fav_side=fav_side,
            fav_price_at_signal=fav_price_at_signal,
            limit_leg_side=limit_leg_side,
            limit_leg_price=limit_leg_price,
            market_close_time=market_close_time,
            market_leg_side=market_leg_side,
            market_leg_price_at_signal=market_leg_price_at_signal,
            market_leg_ticker=market_leg_ticker,
            limit_leg_ticker=limit_leg_ticker,
        )
        return row.id


def get_recent_signals(limit: int = 100) -> list[dict]:
    """Read-back helper (for auditing / future training scripts)."""
    init_db()
    with db.connection_context():
        rows = (LimitArbSignal
                .select()
                .order_by(LimitArbSignal.created_at.desc())
                .limit(limit))
        return [_row_to_dict(r) for r in rows]


def get_unobserved_signals(min_age_seconds: int, max_age_seconds: int, limit: int = 500) -> list[dict]:
    """Rows eligible for outcome observation: not yet observed
    (`outcome_observed_at IS NULL`) and whose age falls in
    [min_age_seconds, max_age_seconds] -- see
    workers/outcome_observer_worker.py module docstring for why that
    window exists and how the bounds were chosen."""
    init_db()
    now = datetime.utcnow()
    with db.connection_context():
        rows = (LimitArbSignal
                .select()
                .where(LimitArbSignal.outcome_observed_at.is_null())
                .order_by(LimitArbSignal.created_at.asc())
                .limit(limit))
        out = []
        for r in rows:
            age = (now - r.created_at).total_seconds()
            if min_age_seconds <= age <= max_age_seconds:
                out.append(_row_to_dict(r))
        return out


def record_outcome(*, signal_id: int, observed_price_move: float | None,
                    limit_leg_filled: str) -> None:
    """Writes back the observation result for one signal (see
    workers/outcome_observer_worker.py). Idempotent by design at the
    caller level (the worker only selects rows with
    outcome_observed_at IS NULL), but this function itself will happily
    overwrite -- it doesn't re-check, callers own that guarantee.

    Fail-closed: observed_price_move stays None if it genuinely
    couldn't be computed (legacy row missing the snapshot columns, or
    the market is gone) -- never a fabricated 0.0."""
    init_db()
    with db.connection_context():
        (LimitArbSignal
         .update(outcome_observed_at=datetime.utcnow(),
                  observed_price_move=observed_price_move,
                  limit_leg_filled=limit_leg_filled)
         .where(LimitArbSignal.id == signal_id)
         .execute())


def get_open_signals(limit: int = 500) -> list[dict]:
    """Rows eligible for the exit-monitoring loop (see
    workers/limitarb_monitor_worker.py): `monitoring_status == 'open'`."""
    init_db()
    with db.connection_context():
        rows = (LimitArbSignal
                .select()
                .where(LimitArbSignal.monitoring_status == 'open')
                .order_by(LimitArbSignal.created_at.asc())
                .limit(limit))
        return [_row_to_dict(r) for r in rows]


def record_monitoring_check(*, signal_id: int, attempt_count: int | None = None,
                             best_limit_gap: float | None = None) -> None:
    """Updates only the running-tally fields for a cycle where none of
    the 3 exit rules fired yet -- monitoring_status stays 'open'.
    Passing None for a field leaves it untouched (only the fields
    actually provided are updated)."""
    init_db()
    updates = {}
    if attempt_count is not None:
        updates['attempt_count'] = attempt_count
    if best_limit_gap is not None:
        updates['best_limit_gap'] = best_limit_gap
    if not updates:
        return
    with db.connection_context():
        (LimitArbSignal
         .update(**updates)
         .where(LimitArbSignal.id == signal_id)
         .execute())


def record_exit_recommendation(*, signal_id: int, exit_reason: str,
                                exit_locked_profit_pct: float | None) -> None:
    """Marks a signal as exit_recommended (rules A/B/C) or closed via a
    non-failure path (e.g. 'limit_filled', 'market_gone') -- see
    workers/limitarb_monitor_worker.py for which reason maps to which
    monitoring_status. exit_locked_profit_pct may be None when it
    genuinely can't be computed -- never fabricated."""
    init_db()
    status = 'closed' if exit_reason in ('limit_filled', 'market_gone') else 'exit_recommended'
    with db.connection_context():
        (LimitArbSignal
         .update(monitoring_status=status,
                  exit_reason=exit_reason,
                  exit_recommended_at=datetime.utcnow(),
                  exit_locked_profit_pct=exit_locked_profit_pct)
         .where(LimitArbSignal.id == signal_id)
         .execute())


def get_unverified_resolved_signals(limit: int = 500) -> list[dict]:
    """Rows eligible for retrospective fill verification (see
    workers/fill_verification_worker.py and
    strategies/fill_verification.py): already resolved by the live
    monitor (`monitoring_status` in ('closed', 'exit_recommended')),
    have a real `limit_leg_ticker` (legacy rows without it aren't
    verifiable -- skipped, documented), and don't already have a
    FillVerification row (this table is written exactly once per
    signal)."""
    init_db()
    with db.connection_context():
        already_verified_ids = {row.signal_id for row in FillVerification.select(FillVerification.signal_id)}
        rows = (LimitArbSignal
                .select()
                .where(
                    LimitArbSignal.monitoring_status.in_(['closed', 'exit_recommended']),
                    LimitArbSignal.limit_leg_ticker.is_null(False),
                )
                .order_by(LimitArbSignal.created_at.asc())
                .limit(limit))
        return [_row_to_dict(r) for r in rows if r.id not in already_verified_ids]


def record_fill_verification(*, signal_id: int, market_id: str, price_touched: bool,
                              post_touch_volume: float | None, queue_ahead_estimate: float | None,
                              our_order_contracts: float | None, limit_filled_verified: bool,
                              fail_reason: str | None) -> int:
    """Persists one FillVerification row (write-once per signal_id, see
    get_unverified_resolved_signals -- this function itself doesn't
    re-check, callers own that guarantee, same pattern as
    record_outcome)."""
    init_db()
    with db.connection_context():
        row = FillVerification.create(
            signal_id=signal_id,
            market_id=market_id,
            price_touched=price_touched,
            post_touch_volume=post_touch_volume,
            queue_ahead_estimate=queue_ahead_estimate,
            our_order_contracts=our_order_contracts,
            limit_filled_verified=limit_filled_verified,
            fail_reason=fail_reason,
        )
        return row.id


def _row_to_dict(r: LimitArbSignal) -> dict:
    return {
        'id': r.id,
        'market_id': r.market_id,
        'case': r.case,
        'avg_volatility': r.avg_volatility,
        'raw_edge': r.raw_edge,
        'price_adjustment': r.price_adjustment,
        'w_edge': r.w_edge,
        'w_volatility': r.w_volatility,
        'return_opportunity': r.return_opportunity,
        'fav_side': r.fav_side,
        'fav_price_at_signal': r.fav_price_at_signal,
        'limit_leg_side': r.limit_leg_side,
        'limit_leg_price': r.limit_leg_price,
        'market_leg_side': r.market_leg_side,
        'market_leg_price_at_signal': r.market_leg_price_at_signal,
        'market_leg_ticker': r.market_leg_ticker,
        'limit_leg_ticker': r.limit_leg_ticker,
        'created_at': r.created_at.isoformat(),
        'outcome_observed_at': r.outcome_observed_at.isoformat() if r.outcome_observed_at else None,
        'observed_price_move': r.observed_price_move,
        'limit_leg_filled': r.limit_leg_filled,
        'market_close_time': r.market_close_time,
        'monitoring_status': r.monitoring_status,
        'attempt_count': r.attempt_count,
        'best_limit_gap': r.best_limit_gap,
        'exit_reason': r.exit_reason,
        'exit_recommended_at': r.exit_recommended_at.isoformat() if r.exit_recommended_at else None,
        'exit_locked_profit_pct': r.exit_locked_profit_pct,
    }
