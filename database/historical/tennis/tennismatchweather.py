from peewee import CharField, DateTimeField, FloatField, ForeignKeyField, Model

from database.db import db_historical
from database.historical.tennis.tennisscannedmatch import TennisScannedMatch


class HistoricalBaseModel(Model):
    class Meta:
        database = db_historical


class TennisMatchWeather(HistoricalBaseModel):
    """Clima real (temperatura/humedad/viento, Open-Meteo) en 3 momentos
    de cada partido resuelto -- pedido explícito del usuario 2026-08-01,
    tras encontrar evidencia real de que Svitolina rinde peor con clima
    húmedo, para eventualmente estudiar si hay patrones reales de
    pérdidas de jugadores top-100 asociados a temperatura/humedad.

    `phase`: 'before' (momento de detección del partido, `detected_at`),
    'during' (punto medio real entre el primer tick observado y el
    settlement -- proxy razonable de "durante el partido" ya que no
    tenemos la hora exacta de inicio real, documentado como aproximación),
    'end' (momento real de settlement, `settled_at`).

    Ver outer_apis/sports/tennis/match_weather.py."""

    class Meta:
        table_name = "tennis_match_weather"
        indexes = ((("match", "phase"), True),)  # un solo registro por (partido, fase)

    match = ForeignKeyField(TennisScannedMatch, backref="weather", on_delete="CASCADE")
    phase = CharField()  # "before" | "during" | "end"
    observed_at = DateTimeField()  # timestamp real usado para pedir el clima
    temperature_c = FloatField(null=True)
    humidity_pct = FloatField(null=True)
    wind_kmh = FloatField(null=True)
    fetched_at = DateTimeField()
