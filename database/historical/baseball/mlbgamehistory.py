from peewee import BooleanField, CharField, DateTimeField, FloatField, IntegerField, Model, TextField

from database.db import db_historical


class HistoricalBaseModel(Model):
    class Meta:
        database = db_historical


class MlbGameHistory(HistoricalBaseModel):
    """Caché LOCAL profunda de partidos reales de MLB, análoga a
    `TmlDatabaseMatch` para tenis -- 2026-08-04, pedido explícito del
    usuario tras notar que `TeamMatchHistory` (tabla genérica de
    deportes de equipo, usada para el Elo en vivo) NO es un histórico
    real: `outer_apis/sports/baseball/ratings.py::get_team_ratings`
    solo pide una ventana RODANTE de 45 días cada vez que refresca, por
    diseño (Elo en vivo, no backtesting) -- nunca acumula hacia atrás.

    1 fila = 1 partido real (a diferencia de `TeamMatchHistory`, que
    guarda 2 filas por partido -- una por cada equipo, ver docstring de
    `database/historical/team_sports/writer.py::compute_and_persist_elo`).
    Incluye el linescore completo (carreras por entrada, JSON) desde el
    momento del backfill -- evita un segundo fetch de red para
    análisis futuros que necesiten el detalle por entrada (ej. el
    checkpoint F5 de `scripts/mlb_comeback_analysis.py`).

    Poblada SOLO por `scripts/mlb_local_backfill.py` (fetch de red
    deliberado, fuera del loop de análisis) -- mismo criterio DB-first
    que `tml_database_local_backfill.py` para tenis: nunca tocar la red
    durante una corrida de análisis/backtest."""

    class Meta:
        table_name = "mlb_game_history"
        indexes = (
            (("game_pk",), True),
        )

    game_pk = IntegerField()
    date = CharField()  # "YYYY-MM-DD"
    home_team = CharField()
    away_team = CharField()
    home_won = BooleanField()
    home_runs_final = IntegerField(null=True)
    away_runs_final = IntegerField(null=True)
    # JSON: [{"num": int, "home_runs": int|None, "away_runs": int|None}, ...]
    # -- guardado tal cual lo entrega la fuente real, ver
    # `outer_apis/scrapers/baseball/mlb_stats_scraper.py::get_linescore`.
    innings_json = TextField(null=True)
    fetched_at = DateTimeField()
    # 2026-08-05, pedido explícito del usuario ("adapta el backfill de
    # clima de tenis para los otros deportes") -- a diferencia de
    # tenis (sin hora real de inicio, snapshot aproximado a las 14:00
    # UTC), MLB SÍ trae hora real del primer lanzamiento (`gameDate` de
    # statsapi.mlb.com) -- guardada tal cual, sin aproximar nada.
    game_datetime_utc = CharField(null=True)  # ISO 8601 real, ej. "2024-06-01T19:07:00Z"
    temperature_c = FloatField(null=True)
    humidity_pct = FloatField(null=True)
    wind_kmh = FloatField(null=True)
    weather_fetched_at = DateTimeField(null=True)
