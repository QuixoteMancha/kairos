from peewee import CharField, DateTimeField, FloatField, IntegerField, Model

from database.db import db_historical


class HistoricalBaseModel(Model):
    class Meta:
        database = db_historical


class TennisAbstractElo(HistoricalBaseModel):
    """Elo de carrera + por superficie real (hElo/cElo/gElo), fuente
    Tennis Abstract (Jeff Sackmann), ver
    outer_apis/scrapers/tennis/tennisabstract_scraper.py::get_elo_ratings.
    Snapshot semanal (misma cadencia con la que el sitio la actualiza) --
    una fila nueva por fetch fresco, no upsert, mismo criterio que
    TennisPlayerRanking/TennisServeCalibration."""

    class Meta:
        table_name = "tennis_abstract_elo"
        indexes = ((("player_name", "tour"), False),)

    player_name = CharField()
    tour = CharField()  # "ATP" | "WTA"
    elo = FloatField()
    h_elo = FloatField(null=True)  # Hard
    c_elo = FloatField(null=True)  # Clay
    g_elo = FloatField(null=True)  # Grass
    atp_rank = IntegerField(null=True)  # ranking oficial tal como lo reporta TA (ATP o WTA según `tour`)
    fetched_at = DateTimeField()


class TennisAbstractYelo(HistoricalBaseModel):
    """Elo de la TEMPORADA en curso ("yElo") + récord W/L, fuente Tennis
    Abstract, ver
    outer_apis/scrapers/tennis/tennisabstract_scraper.py::get_season_yelo.
    Candidato a reemplazar el win-rate naive de forma reciente por algo
    metodológicamente más sólido -- ver
    outer_apis/sports/tennis/tennisabstract_elo.py."""

    class Meta:
        table_name = "tennis_abstract_yelo"
        indexes = ((("player_name", "tour"), False),)

    player_name = CharField()
    tour = CharField()
    yelo = FloatField()
    wins = IntegerField(null=True)
    losses = IntegerField(null=True)
    fetched_at = DateTimeField()
