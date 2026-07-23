"""
Peewee model for paper (simulated) trades opened against LimitArb signals
(see strategies/limit_arb.py and database/accounting/limitarbsignal.py).

PURPOSE: this table exists to calibrate the Kelly-fractional position
sizing (see strategies/paper_trading.py) with REAL outcome data instead
of staying a hand-picked guess forever -- every LimitArb signal that
clears the minimum position size gets a simulated paper position here,
and its eventual close (via workers/limitarb_monitor_worker.py's exit
recommendations) feeds recalibrate_kelly_factor().

HARD RED LINE -- READ FIRST: Kairos has NO real order execution anywhere.
This table is 100% simulated/paper money (see PaperAccount.bankroll_usd,
starts at $200 fake USDC) -- it NEVER touches a real Kalshi account or
any other real account. Nothing here should ever be read as evidence of
real capital at risk.
"""

from datetime import datetime

from peewee import CharField, DateTimeField, FloatField, IntegerField, Model

from database.db import db


class BaseModel(Model):
    class Meta:
        database = db


class PaperTrade(BaseModel):
    """One row per LimitArbSignal for which a simulated paper position was
    opened (see strategies/paper_trading.py:open_paper_trade). Peewee
    doesn't enforce a real FK here (same pattern as market_id elsewhere in
    this package) -- signal_id is a logical reference to
    LimitArbSignal.id, kept as a plain indexed IntegerField."""

    signal_id = IntegerField(index=True)
    market_id = CharField(index=True)  # denormalized for direct queries
    case = CharField()  # 'favorite_overvalued' | 'favorite_undervalued', copied from the signal

    opened_at = DateTimeField(default=datetime.utcnow, index=True)

    position_size_usd = FloatField()
    p_success_proxy_at_open = FloatField()  # proxy used at open time, needed to recalibrate later
    kelly_fraction_used = FloatField()  # fraction of equity actually applied (post 0.5x multiplier + 10% cap)

    market_leg_price = FloatField()  # market leg entry price (already filled, it's a market order)
    market_leg_fee = FloatField()  # fee already paid (market leg always "fills")

    status = CharField(default='open')  # 'open' | 'closed'
    closed_at = DateTimeField(null=True)
    # same vocabulary as LimitArbSignal.exit_reason, plus 'market_gone_unresolved'
    close_reason = CharField(null=True)  # 'limit_filled' | 'timeout' | 'trailing_stop' | 'time_decay' | 'market_gone_unresolved'
    realized_pnl_usd = FloatField(null=True)  # None when genuinely not computable -- never fabricated

    # Added 2026-07-23: the market leg's price at close time (live quote,
    # or a terminal 0.0/1.0 reconstructed from a real Kalshi settlement
    # 'result' via get_market_any_status -- see workers/limitarb_monitor_
    # worker.py). Lets PnL/close audits see exactly what price closed the
    # position instead of only the derived realized_pnl_usd.
    close_market_leg_price = FloatField(null=True)
