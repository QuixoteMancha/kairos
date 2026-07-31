from peewee import CharField, DateTimeField, FloatField, IntegerField, Model

from database.db import db_historical


class HistoricalBaseModel(Model):
    class Meta:
        database = db_historical


class TennisAbstractCareerSplit(HistoricalBaseModel):
    """Win% de partido REAL por superficie (carrera completa), fuente
    Tennis Abstract per-player jsfrag, ver
    outer_apis/scrapers/tennis/tennisabstract_player_scraper.py::get_career_splits_by_surface.
    Snapshot -- una fila nueva por (player, surface) por fetch fresco,
    reemplaza la anterior en efecto vía la query DB-first que toma la más
    reciente (no upsert explícito, mismo criterio que TennisAbstractElo)."""

    class Meta:
        table_name = "tennis_abstract_career_split"
        indexes = ((("player_name", "surface"), False),)

    player_name = CharField()
    surface = CharField()  # "Hard" | "Clay" | "Grass"
    matches = IntegerField(null=True)
    wins = IntegerField(null=True)
    losses = IntegerField(null=True)
    win_pct = FloatField(null=True)
    hold_pct = FloatField(null=True)
    break_pct = FloatField(null=True)
    ace_pct = FloatField(null=True)
    df_pct = FloatField(null=True)
    fetched_at = DateTimeField()


class TennisAbstractH2H(HistoricalBaseModel):
    """Récord real cabeza-a-cabeza contra rivales FRECUENTES (no todos),
    fuente Tennis Abstract per-player jsfrag, ver
    outer_apis/scrapers/tennis/tennisabstract_player_scraper.py::get_head_to_head."""

    class Meta:
        table_name = "tennis_abstract_h2h"
        indexes = ((("player_name", "opponent_name"), False),)

    player_name = CharField()
    opponent_name = CharField()
    meetings = IntegerField()
    wins = IntegerField(null=True)
    losses = IntegerField(null=True)
    win_pct = FloatField(null=True)
    fetched_at = DateTimeField()
