"""
Call surface for logging/reading LimitArb signals (see
database/accounting/limitarbsignal.py for the schema and why it exists —
this is the groundwork for eventually recalibrating the price_adjustment
regression weights against real outcomes, not a finished training loop).
"""

from datetime import datetime

from database.db import db, init_db
from database.accounting.limitarbsignal import LimitArbSignal


def log_signal(*, market_id: str, avg_volatility: float, raw_edge: float,
                price_adjustment: float, w_edge: float, w_volatility: float,
                case: str, return_opportunity: float,
                fav_side: str | None = None, fav_price_at_signal: float | None = None,
                limit_leg_side: str | None = None, limit_leg_price: float | None = None) -> None:
    """Persists one LimitArb signal. Fail-closed at the call site (see
    strategies/limit_arb.py, wrapped in try/except): a logging failure
    must never crash or block a scan.

    The fav_*/limit_leg_* snapshot params are optional (default None) so
    older callers keep working, but strategies/limit_arb.py now always
    passes them -- they're what workers/outcome_observer_worker.py needs
    to later diff against the real market price and know which side/price
    to check for a fill."""
    init_db()
    with db.connection_context():
        LimitArbSignal.create(
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
        )


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
        'created_at': r.created_at.isoformat(),
        'outcome_observed_at': r.outcome_observed_at.isoformat() if r.outcome_observed_at else None,
        'observed_price_move': r.observed_price_move,
        'limit_leg_filled': r.limit_leg_filled,
    }
