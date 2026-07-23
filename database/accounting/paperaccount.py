"""
Peewee model for the paper-trading account (singleton, one row, id=1).
See database/accounting/papertrade.py for the paper-trading design and
the hard red line: this is 100% simulated money, never real.
"""

from datetime import datetime

from peewee import DateTimeField, FloatField, Model

from database.db import db


class BaseModel(Model):
    class Meta:
        database = db


class PaperAccount(BaseModel):
    """Singleton row (id=1) tracking the simulated paper-trading bankroll
    and the current Kelly calibration factor (see
    strategies/paper_trading.py:recalibrate_kelly_factor)."""

    bankroll_usd = FloatField(default=200.0)  # simulated starting bankroll, updated on every PaperTrade close
    kelly_calibration_factor = FloatField(default=1.0)
    last_recalibrated_at = DateTimeField(null=True)
    updated_at = DateTimeField(default=datetime.utcnow)
