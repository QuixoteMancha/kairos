"""
Peewee model for Kairos operational/accounting data (LimitArb signals
today, future orders/fills/PnL — see database/db.py docstring for the
two-database split: this lives in `db`/`DB_PATH`, the "definitive"
volume, not `db_historical`).
"""

from datetime import datetime

from peewee import CharField, DateTimeField, FloatField, Model

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

    # Outcome columns -- NULL until a follow-up mechanism exists to
    # observe them (not built yet, see class docstring). Never populate
    # with a guessed/default value.
    outcome_observed_at = DateTimeField(null=True)
    observed_price_move = FloatField(null=True)  # actual |price change| observed for the favorite side
    limit_leg_filled = CharField(null=True)  # 'yes' | 'no' | None (unknown/not checked)
