from peewee import DateTimeField, FloatField, ForeignKeyField, Model

from database.db import db_historical
from database.historical.tennis.tennisscannedmatch import TennisScannedMatch


class HistoricalBaseModel(Model):
    class Meta:
        database = db_historical


class TennisMatchProbabilityTick(HistoricalBaseModel):
    """Un tick por cada re-chequeo periódico (~1 min, ver
    workers/tennis_match_tick_worker.py) de un partido registrado en
    `TennisScannedMatch`, mientras esté abierto (`winner`/`settled_at`
    todavía NULL).

    Guarda AMBAS probabilidades a propósito (pedido explícito del
    usuario): `market_price_a` (lo que el mercado real de Kalshi está
    cotizando en ese instante -- el "ground truth" observado) y
    `model_fair_a` (lo que `TennisFModel` calcula en ese mismo instante
    con los mismos datos disponibles) -- comparar ambas series a lo largo
    del tiempo es exactamente la validación de calibración que se busca:
    ¿el modelo trackea el precio real, sistemáticamente se adelanta/
    atrasa, o diverge en ciertas condiciones (superficie/ranking/etc.)?

    `model_fair_a` es nullable: un fallo momentáneo del modelo (dato
    externo caído, etc.) no debe impedir registrar igual el precio real
    de mercado en ese tick -- fail-closed por campo, no por fila completa."""

    class Meta:
        table_name = "tennis_match_probability_tick"
        indexes = ((("match", "observed_at"), False),)

    match = ForeignKeyField(TennisScannedMatch, backref="ticks", on_delete="CASCADE")
    observed_at = DateTimeField()
    market_price_a = FloatField(null=True)
    model_fair_a = FloatField(null=True)
