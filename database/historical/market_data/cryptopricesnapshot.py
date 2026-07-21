"""
Peewee model for the HISTORICAL/reference database — rescued verbatim
from the pre-"extreme makeover" schema, see kalshimarkettick.py's
docstring for the full rescue context (same investigation, same volume).
"""

from peewee import CharField, DateTimeField, FloatField, Model

from database.db import db_historical


class HistoricalBaseModel(Model):
    class Meta:
        database = db_historical


class CryptoPriceSnapshot(HistoricalBaseModel):
    """Real OHLCV candles, 11 crypto symbols, ~3 weeks of history already
    in the rescued volume — underlying-asset price reference for
    crypto_15m calibration (distinct from the Kalshi-side tick prices in
    KalshiMarketTick)."""

    class Meta:
        table_name = "cryptopricesnapshot"

    created_at = DateTimeField()
    updated_at = DateTimeField()
    symbol = CharField()
    timestamp = DateTimeField()
    open = FloatField()
    high = FloatField()
    low = FloatField()
    close = FloatField()
    volume = FloatField()
