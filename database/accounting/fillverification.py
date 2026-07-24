"""
Peewee model for retrospective fill-verification of the LIMIT leg of a
LimitArb signal (see strategies/limit_arb.py and
database/accounting/limitarbsignal.py for the two-leg design).

WHY THIS EXISTS
----------------
`workers/limitarb_monitor_worker.py` only takes a 30s-interval SNAPSHOT
of the current price to guess whether the limit leg "would be
marketable" -- it can miss a price that crossed the limit and came back
between two checks, and it never looks at real EXECUTED trades. This
table records a ONE-TIME, retrospective, post-settlement verification
per signal, using Kalshi's real trade tape (`outer_apis/kalshi.py:get_trades`,
`GET /markets/trades`) instead of a live snapshot -- see
strategies/fill_verification.py for the actual computation and its
documented approximations (this is still a proxy, not a real fill
confirmation -- Kairos has no real order execution anywhere, hard red
line, see memory kairos-no-real-execution-user-trades-manually).

One row per LimitArbSignal, written exactly once (see
workers/fill_verification_worker.py: only signals without an existing
FillVerification row and already resolved by the monitor worker are
considered).
"""

from datetime import datetime

from peewee import BooleanField, CharField, DateTimeField, FloatField, IntegerField, Model

from database.db import db


class BaseModel(Model):
    class Meta:
        database = db


class FillVerification(BaseModel):
    """Retrospective fill-verification result for one LimitArbSignal's
    limit leg, computed once the underlying event has settled (see
    strategies/fill_verification.py:compute_fill_verification for the
    exact algorithm and all documented simplifications -- most notably
    that `queue_ahead_estimate` is a proxy derived from historical trade
    sizes, NOT a real order-book-depth measurement, since Kalshi does
    not expose historical order book depth)."""

    signal_id = IntegerField(index=True)
    market_id = CharField(index=True)  # denormalized, copied from the signal

    verified_at = DateTimeField(default=datetime.utcnow)

    # Did any real trade execute on limit_leg_ticker, after signal.created_at,
    # at a yes price <= limit_leg_price?
    price_touched = BooleanField()

    # Sum of count_fp across qualifying trades. None (not 0.0) when
    # price_touched is False -- never fabricate a "calculated zero".
    post_touch_volume = FloatField(null=True)

    # Proxy for how much size was likely queued ahead of our hypothetical
    # order: average trade size on limit_leg_ticker BEFORE signal.created_at.
    # See strategies/fill_verification.py module docstring for the
    # optimistic-bias caveat (0.0 when no prior trade history exists).
    queue_ahead_estimate = FloatField(null=True)

    # position_size_usd / limit_leg_price from the associated PaperTrade,
    # if one exists for this signal. None if no PaperTrade was opened
    # (e.g. signal never cleared MIN_POSITION_USD) -- never fabricated.
    our_order_contracts = FloatField(null=True)

    limit_filled_verified = BooleanField()
    # 'no_price_touch' | 'insufficient_volume' | 'queue_position' | None
    # (either limit_filled_verified is True, or the reason genuinely
    # isn't computable -- e.g. price touched but our_order_contracts is
    # unknown -- documented in strategies/fill_verification.py).
    fail_reason = CharField(null=True)
