"""
Call surface for logging/reading LimitArb signals (see
database/accounting/limitarbsignal.py for the schema and why it exists —
this is the groundwork for eventually recalibrating the price_adjustment
regression weights against real outcomes, not a finished training loop).
"""

from database.db import db, init_db
from database.accounting.limitarbsignal import LimitArbSignal


def log_signal(*, market_id: str, avg_volatility: float, raw_edge: float,
                price_adjustment: float, w_edge: float, w_volatility: float,
                case: str, return_opportunity: float) -> None:
    """Persists one LimitArb signal. Fail-closed at the call site (see
    strategies/limit_arb.py, wrapped in try/except): a logging failure
    must never crash or block a scan."""
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
        )


def get_recent_signals(limit: int = 100) -> list[dict]:
    """Read-back helper (for auditing / future training scripts)."""
    init_db()
    with db.connection_context():
        rows = (LimitArbSignal
                .select()
                .order_by(LimitArbSignal.created_at.desc())
                .limit(limit))
        return [
            {
                'market_id': r.market_id,
                'case': r.case,
                'avg_volatility': r.avg_volatility,
                'raw_edge': r.raw_edge,
                'price_adjustment': r.price_adjustment,
                'w_edge': r.w_edge,
                'w_volatility': r.w_volatility,
                'return_opportunity': r.return_opportunity,
                'created_at': r.created_at.isoformat(),
                'outcome_observed_at': r.outcome_observed_at.isoformat() if r.outcome_observed_at else None,
                'observed_price_move': r.observed_price_move,
                'limit_leg_filled': r.limit_leg_filled,
            }
            for r in rows
        ]
