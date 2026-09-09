"""Series point-in-time para mercados climáticos de Kalshi.

No contiene señales ni órdenes: conserva el contrato, precio ejecutable y
forecast que existían al momento de observarlo para backtests sin leakage.
"""

from peewee import CharField, DateField, DateTimeField, FloatField, TextField

from database.base_models import HistoricalBaseModel


class WeatherMarketSnapshot(HistoricalBaseModel):
    class Meta:
        table_name = "weather_market_snapshot"
        indexes = ((('ticker', 'observed_at'), True), (('status',), False))

    ticker = CharField()
    event_ticker = CharField(null=True)
    series_ticker = CharField(null=True)
    city_code = CharField(null=True)
    contract_kind = CharField(null=True)  # high_temp / unsupported; nunca se adivina
    target_date = DateField(null=True)
    title = TextField(null=True)
    yes_ask = FloatField(null=True)
    no_ask = FloatField(null=True)
    close_time = DateTimeField(null=True)
    status = CharField()
    result = CharField(null=True)
    forecast_json = TextField(null=True)
    raw_market_json = TextField()
    observed_at = DateTimeField(index=True)
