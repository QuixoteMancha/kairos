"""Series point-in-time para mercados climáticos de Kalshi.

No contiene señales ni órdenes: conserva el contrato, precio ejecutable y
forecast que existían al momento de observarlo para backtests sin leakage.
"""

from peewee import (
    BooleanField, CharField, DateField, DateTimeField, FloatField,
    ForeignKeyField, TextField,
)

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


class WeatherStation(HistoricalBaseModel):
    class Meta:
        table_name = "weather_station"

    code = CharField(unique=True)
    latitude = FloatField()
    longitude = FloatField()
    timezone = CharField()
    raw_json = TextField()
    fetched_at = DateTimeField()


class WeatherContract(HistoricalBaseModel):
    """Latest contract state; immutable rule/outcome revisions live in WeatherEvidence."""
    class Meta:
        table_name = "weather_contract"

    ticker = CharField(unique=True)
    series = CharField(index=True)
    event_ticker = CharField(null=True)
    station = ForeignKeyField(WeatherStation, null=True, on_delete="SET NULL")
    station_match_verified = BooleanField(default=False)
    target_date = DateField(null=True)
    kind = CharField(default="unsupported")
    settlement_source = CharField(null=True)
    status = CharField()
    result = CharField(null=True)
    expiration_value = CharField(null=True)
    close_time = DateTimeField(null=True)
    raw_json = TextField()
    fetched_at = DateTimeField()


class WeatherEvidence(HistoricalBaseModel):
    """Immutable source revisions. Historical initialization != public availability."""
    class Meta:
        table_name = "weather_evidence"
        indexes = ((("kind", "identity", "sha256"), True),)

    kind = CharField(index=True)
    identity = CharField(index=True)
    contract = ForeignKeyField(WeatherContract, null=True, on_delete="SET NULL")
    station = ForeignKeyField(WeatherStation, null=True, on_delete="SET NULL")
    target_date = DateField(null=True, index=True)
    initialized_at = DateTimeField(null=True)
    available_at = DateTimeField(null=True)
    captured_at = DateTimeField()
    source_url = TextField()
    parameters_json = TextField()
    payload_json = TextField()
    sha256 = CharField()


class WeatherCaptureJob(HistoricalBaseModel):
    """Resumable bounded request queue; HTTP failures retain cursor and retry later."""
    class Meta:
        table_name = "weather_capture_job"

    key = CharField(unique=True)
    cursor = TextField(null=True)
    next_at = DateTimeField()
    error = TextField(null=True)
