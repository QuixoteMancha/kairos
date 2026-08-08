"""
Peewee model for the HISTORICAL/reference database (`db_historical`,
`DB_HISTORICAL_PATH` — see database/db.py docstring). Rescued from the
pre-"extreme makeover" architecture's Peewee schema, VERBATIM column
definitions (same types/names/nullability), confirmed against the real
production data still living in the mini-pc's `kairos_kairos_data` volume
(inspected directly via SSH 2026-07-21, not guessed).
"""

from peewee import CharField, DateTimeField, FloatField

from database.base_models import HistoricalBaseModel


class KalshiMarketTick(HistoricalBaseModel):
    """Real per-trade ticks from Kalshi crypto_15m markets. 11.5M+ real
    rows already in the rescued volume (BTC/ETH/SOL/XRP/DOGE/BNB/ZEC/
    NEAR/HYPE, 2026-07-11 onward) — useful for volatility/price-path
    calibration of short-duration crypto markets."""

    class Meta:
        table_name = "kalshimarkettick"

    created_at = DateTimeField()
    updated_at = DateTimeField()
    trade_id = CharField()
    ticker = CharField()
    event_ticker = CharField(null=True)
    created_time = DateTimeField()
    yes_price = FloatField(null=True)
    no_price = FloatField(null=True)
    count = FloatField(null=True)
    taker_side = CharField(null=True)
    fetched_at = DateTimeField()
