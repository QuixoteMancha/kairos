from peewee import CharField, ForeignKeyField, TextField

from database.historical.tennis.tennisscannedmatch import HistoricalBaseModel
from database.historical.tennis.chartedmatch import ChartedMatch


class ChartedMatchStats(HistoricalBaseModel):
    """Estadísticas reales por set y totales (`Overview.csv` del Match
    Charting Project, ver `match_charting_scraper.get_overview_stats`)
    para un `ChartedMatch` -- 2026-08-02, pedido explícito del usuario.

    `raw_stats_json`: MISMO shape que `TennisMatchStats.raw_stats_json`
    ({"match": {stat: [p1_val, p2_val]}, "set_1": {...}, ...}) a
    propósito, para que ambas fuentes (Flashscore vía TennisMatchStats,
    Sackmann vía esta tabla) sean comparables/combinables con el mismo
    código de lectura."""

    class Meta:
        table_name = "charted_match_stats"
        indexes = ((("match",), True),)

    match = ForeignKeyField(ChartedMatch, backref="stats", on_delete="CASCADE", unique=True)
    source = CharField(default="sackmann_match_charting_project")
    raw_stats_json = TextField()
