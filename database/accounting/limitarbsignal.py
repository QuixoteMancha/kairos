"""
Peewee model for Kairos operational/accounting data (LimitArb signals
today, future orders/fills/PnL — see database/db.py docstring for the
two-database split: this lives in `db`/`DB_PATH`, the "definitive"
volume, not `db_historical`).
"""

from datetime import datetime

from peewee import CharField, DateTimeField, FloatField, IntegerField, Model

from database.db import db


class BaseModel(Model):
    class Meta:
        database = db


class LimitArbSignal(BaseModel):
    """One row per LimitArb opportunity reported (see
    strategies/limit_arb.py). Logged so the price_adjustment regression
    weights (W_EDGE/W_VOLATILITY, currently fixed initial values 0.4/0.6)
    can eventually be recalibrated against REAL outcomes instead of
    staying hand-picked forever.

    IMPORTANT — this table only records the SIGNAL (inputs + computed
    adjustment) at generation time. It does NOT yet record what actually
    happened to the price afterward (did it move by price_adjustment?
    more? less? not at all? did the limit leg actually fill?) — that
    requires a separate follow-up mechanism (re-checking the market some
    time after the signal, or a fills-webhook once order execution
    exists) that isn't built yet. Until `outcome_observed_at` is
    populated, this table can only be used to inspect signal DISTRIBUTION
    (e.g. "how often are edges implausibly large"), not to train a
    real regression — that needs the outcome columns filled in first.
    Fail-closed: outcome fields default to NULL, never a fabricated 0.0
    that could be misread as "confirmed no movement".
    """

    market_id = CharField(index=True)
    case = CharField()  # 'favorite_overvalued' | 'favorite_undervalued'

    avg_volatility = FloatField()
    raw_edge = FloatField()  # |fav_price - fav_fair| at signal time
    price_adjustment = FloatField()  # W_EDGE*raw_edge + W_VOLATILITY*(avg_volatility/2)
    w_edge = FloatField()  # regression weight used for this signal (for auditability if weights change over time)
    w_volatility = FloatField()
    return_opportunity = FloatField()  # profit reported, derived from price_adjustment (see limit_arb.py)

    created_at = DateTimeField(default=datetime.utcnow, index=True)

    # Snapshot columns added 2026-07-21 (see workers/outcome_observer_worker.py)
    # to make outcome observation possible. The original schema only
    # logged the computed price_adjustment/case, not the raw favorite/
    # limit-leg prices at signal time -- without those there is nothing
    # to diff against later. All nullable: rows written before this
    # change simply won't have them (see backfill note below), and
    # `LimitArbSignal.create()` calls that don't pass them still work
    # (Peewee doesn't require every field).
    fav_side = CharField(null=True)  # 'a' | 'b' -- which side was the favorite at signal time
    fav_price_at_signal = FloatField(null=True)  # favorite leg's current_*_price at signal time
    limit_leg_side = CharField(null=True)  # 'a' | 'b' -- side the limit order was planned on
    limit_leg_price = FloatField(null=True)  # planned limit price for that leg

    # Added 2026-07-23 (see workers/limitarb_monitor_worker.py) to close a
    # coverage gap: exit_locked_profit_pct could only be computed for case
    # 'favorite_undervalued' (market leg == fav side, already snapshotted
    # above) -- for 'favorite_overvalued' the market leg is the DOG side,
    # which had no entry-price snapshot anywhere. market_leg_side/price
    # cover BOTH cases explicitly (redundant with fav_price_at_signal for
    # the undervalued case, but simpler than branching two different
    # snapshot fields at read time). Nullable: rows logged before this
    # change won't have it -- exit_locked_profit_pct stays None for those,
    # same fail-closed behavior as before, never fabricated.
    market_leg_side = CharField(null=True)  # 'a' | 'b' -- side the market order was/would be placed on
    market_leg_price_at_signal = FloatField(null=True)  # market leg's price at signal time (entry price)

    # Outcome columns -- NULL until a follow-up mechanism exists to
    # observe them. Populated by workers/outcome_observer_worker.py.
    # Fail-closed: never populate with a guessed/default value -- if the
    # snapshot columns above are missing (pre-2026-07-21 row) or the
    # market can't be refetched, leave observed_price_move NULL rather
    # than fabricate a number, and record why in limit_leg_filled.
    outcome_observed_at = DateTimeField(null=True)
    observed_price_move = FloatField(null=True)  # actual |price change| observed for the favorite side
    # 'yes' | 'no' | 'unknown_legacy_row' (pre-snapshot-columns row, can't
    # be inferred) | 'market_gone' (ticker no longer returned by Kalshi --
    # settled/removed) | None (not yet observed)
    limit_leg_filled = CharField(null=True)

    # Monitoring/exit-recommendation columns added 2026-07-22 (see
    # workers/limitarb_monitor_worker.py). This worker NEVER places or
    # cancels a real order (hard red line, same as everywhere else in
    # this project) -- it only recommends closing/abandoning the plan
    # based on 3 rules (timeout / trailing-stop-near-pinch / time-decay).
    market_close_time = DateTimeField(null=True)  # snapshot of market['close_time'] at log_signal time
    monitoring_status = CharField(default='open')  # 'open' | 'exit_recommended' | 'closed'
    attempt_count = IntegerField(default=0)  # rule A: consecutive 30s checks where limit leg wasn't marketable
    best_limit_gap = FloatField(null=True)  # rule B: min observed |current_limit_side_price - limit_leg_price|
    exit_reason = CharField(null=True)  # 'timeout' | 'trailing_stop' | 'time_decay' | 'limit_filled' | 'market_gone'
    exit_recommended_at = DateTimeField(null=True)
    exit_locked_profit_pct = FloatField(null=True)  # can be negative (rule C); None if not computable -- never fabricated

    # Added 2026-07-23: real Kalshi tickers of each leg, to fix the
    # cross-ticket monitoring bug (market_id for tennis/team-sports is a
    # synthetic event_ticker, never fetchable via get_market -- see
    # outer_apis/kalshi_tennis_markets.py / kalshi_team_sport_markets.py). For
    # single-ticket markets these just equal market_id. Nullable: rows
    # logged before this fix won't have them -- monitor/observer workers
    # fall back to the old market_id-based fetch for those, unchanged.
    market_leg_ticker = CharField(null=True)  # real Kalshi ticker for the market leg
    limit_leg_ticker = CharField(null=True)  # real Kalshi ticker for the limit leg
