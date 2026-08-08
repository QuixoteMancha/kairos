from peewee import DateTimeField, FloatField, ForeignKeyField

from database.base_models import HistoricalBaseModel
from database.historical.team_sports.mlbscannedmatch import MlbScannedMatch


class MlbMatchProbabilityTick(HistoricalBaseModel):
    """Análogo a `TennisMatchProbabilityTick` -- un tick por cada
    re-chequeo periódico de un partido MLB registrado en
    `MlbScannedMatch`, mientras esté abierto. Guarda AMBAS
    probabilidades: `market_price_a` (precio real de Kalshi en ese
    instante) y `model_fair_a` (`BaseballFModel.calculate_fair_price`
    con los ratings Elo reales disponibles en ese instante) -- misma
    validación de calibración que tenis: comparar la serie real contra
    la serie modelada a lo largo del tiempo."""

    class Meta:
        table_name = "mlb_match_probability_tick"
        indexes = ((("match", "observed_at"), False),)

    match = ForeignKeyField(MlbScannedMatch, backref="ticks", on_delete="CASCADE")
    observed_at = DateTimeField()
    market_price_a = FloatField(null=True)
    model_fair_a = FloatField(null=True)
