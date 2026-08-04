from peewee import CharField, DateTimeField, FloatField, Model

from database.db import db_historical


class HistoricalBaseModel(Model):
    class Meta:
        database = db_historical


class TennisMatchHistory(HistoricalBaseModel):
    """One row per real charted match result for a player — see
    outer_apis/tennis_match_history.py::get_recent_matches (Flashscore-sourced).
    Persisting this builds a real historical match database over time
    (the old RecentMatchHistory/Match tables never got populated) —
    useful for recent-form calculations that don't need to re-scrape
    Flashscore every time, and as raw material for a future richer
    fair-price model (recency-weighted form, surface-specific form, etc.
    — none of that is built yet, this table just makes the raw data
    durable)."""

    class Meta:
        table_name = "tennis_match_history"
        indexes = ((("player_name",), False),)

    player_name = CharField()
    opponent_name = CharField()
    match_date = CharField()  # "YYYY-MM-DD", matches the source's string format
    result = CharField()  # "W" | "L"
    score = CharField(null=True)
    source = CharField()  # "flashscore" | "tennisexplorer_manual"
    fetched_at = DateTimeField()
    # 2026-08-03, pedido explícito del usuario ("agrega a la base los
    # matches que no están con sus respectivos torneos manteniendo las
    # nomenclaturas como están") -- nombre del torneo TAL CUAL viene de
    # la fuente (sin normalizar), agregado DESPUÉS de que la tabla ya
    # existiera -- requiere ALTER TABLE idempotente, ver
    # database/db.py::_add_column_if_missing. Nullable: filas viejas de
    # Flashscore (source="flashscore") nunca tuvieron este dato.
    tournament = CharField(null=True)
    # 2026-08-03, pedido explícito del usuario (backfill masivo top-200
    # WTA vía TennisExplorer) -- round/surface vienen gratis del mismo
    # fetch, agregados DESPUÉS de que la tabla ya existiera, mismo
    # mecanismo de ALTER TABLE idempotente que `tournament`.
    round = CharField(null=True)
    surface = CharField(null=True)
    # 2026-08-03, pedido explícito del usuario ("luego hacer fetch de
    # datos climáticos de match") -- mismo criterio que ChartedMatch
    # (snapshot único a hora fija 14:00 UTC, NO 3-fases, porque tampoco
    # hay hora real de inicio para estos partidos) -- ver
    # scripts/wta_match_history_weather_backfill.py.
    temperature_c = FloatField(null=True)
    humidity_pct = FloatField(null=True)
    wind_kmh = FloatField(null=True)
    weather_fetched_at = DateTimeField(null=True)
