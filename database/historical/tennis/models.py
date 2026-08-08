"""Modelos ESPECÍFICOS de tenis -- 2026-08-08, pedido explícito del
usuario: "definiendo modelos específicos en un solo file dentro de las
carpetas de deportes" (ver docstring de `database/historical/models.py`
para el criterio completo de qué queda genérico vs. qué es específico
de un deporte).

Todo lo que hay acá es SOLO tenis (ATP+WTA): historial de partidos,
cuotas, ranking, calibración de saque, Match Charting Project, cache de
slugs de TennisExplorer, ubicación de torneos. Lo genérico/reusable
(match-tracking + clima, hoy solo usado por tenis pero agnóstico de
deporte en estructura) vive en `database/historical/models.py` --
`database/historical/tennis/__init__.py` reexporta ambos grupos bajo
los nombres históricos (`TennisScannedMatch`, etc.)."""

from peewee import BooleanField, CharField, DateField, DateTimeField, FloatField, ForeignKeyField, IntegerField, TextField

from database.base_models import HistoricalBaseModel


class TennisPlayerBio(HistoricalBaseModel):
    """Fecha de nacimiento real por jugador/a -- cache PERMANENTE
    (nunca cambia, se sirve para siempre sin volver a pedirla). Ver
    `outer_apis/scrapers/tennis/tennisabstract_player_scraper.py::
    get_date_of_birth`."""

    class Meta:
        table_name = "tennis_player_bio"
        indexes = ((("player_name",), True),)

    player_name = CharField(unique=True)
    date_of_birth = DateField()
    fetched_at = DateTimeField()


class ChartedMatch(HistoricalBaseModel):
    class Meta:
        table_name = "charted_match"
        indexes = ((("match_id",), True),)

    match_id = CharField(unique=True)
    tour = CharField()
    player_a_name = CharField()
    player_b_name = CharField()
    match_date = DateField()
    tournament = CharField(null=True)
    round = CharField(null=True)
    surface = CharField(null=True)
    best_of = IntegerField(null=True)
    winner = CharField(null=True)
    temperature_c = FloatField(null=True)
    humidity_pct = FloatField(null=True)
    wind_kmh = FloatField(null=True)
    weather_fetched_at = DateTimeField(null=True)


class ChartedMatchStats(HistoricalBaseModel):
    class Meta:
        table_name = "charted_match_stats"
        indexes = ((("match",), True),)

    match = ForeignKeyField(ChartedMatch, backref="stats", on_delete="CASCADE", unique=True)
    source = CharField(default="sackmann_match_charting_project")
    raw_stats_json = TextField()


class TennisMatchHistory(HistoricalBaseModel):
    class Meta:
        table_name = "tennis_match_history"
        indexes = ((("player_name",), False),)

    player_name = CharField()
    opponent_name = CharField()
    match_date = CharField()
    result = CharField()
    score = CharField(null=True)
    source = CharField()
    fetched_at = DateTimeField()
    tournament = CharField(null=True)
    round = CharField(null=True)
    surface = CharField(null=True)
    temperature_c = FloatField(null=True)
    humidity_pct = FloatField(null=True)
    wind_kmh = FloatField(null=True)
    weather_fetched_at = DateTimeField(null=True)


class TennisHistoricalOdds(HistoricalBaseModel):
    class Meta:
        table_name = "tennis_historical_odds"
        indexes = ((("player_name", "opponent_name", "match_date"), True),)

    player_name = CharField()
    opponent_name = CharField()
    match_date = CharField()
    odds_self = FloatField(null=True)
    odds_opponent = FloatField(null=True)
    source = CharField()
    fetched_at = DateTimeField()


class TmlDatabaseMatch(HistoricalBaseModel):
    class Meta:
        table_name = "tml_database_match"
        indexes = ((("year", "date", "winner_name", "loser_name", "tourney_name"), True),)

    year = IntegerField()
    tourney_name = CharField(null=True)
    level = CharField(null=True)
    date = CharField()
    round = CharField(null=True)
    surface = CharField(null=True)
    best_of = IntegerField(null=True)
    winner_name = CharField()
    winner_rank = IntegerField(null=True)
    winner_age = FloatField(null=True)
    loser_name = CharField()
    loser_rank = IntegerField(null=True)
    loser_age = FloatField(null=True)
    score = CharField(null=True)
    minutes = IntegerField(null=True)


class TennisPlayerRanking(HistoricalBaseModel):
    class Meta:
        table_name = "tennis_player_ranking"
        indexes = ((("player_name", "tour"), False),)

    player_name = CharField()
    tour = CharField()
    ranking = IntegerField()
    country = CharField(null=True)
    fetched_at = DateTimeField()


class TennisServeCalibration(HistoricalBaseModel):
    class Meta:
        table_name = "tennis_serve_calibration"
        indexes = ((("tour", "bucket_min", "bucket_max"), False),)

    tour = CharField()
    bucket_min = IntegerField()
    bucket_max = IntegerField()
    p_serve = FloatField()
    n_matches = IntegerField()
    n_players = IntegerField()
    calibrated_at = DateTimeField()


class TennisSurfaceMatchHistory(HistoricalBaseModel):
    class Meta:
        table_name = "tennis_surface_match_history"
        indexes = ((("player_name",), False),)

    player_name = CharField()
    opponent_name = CharField()
    match_date = CharField()
    surface = CharField()
    result = CharField()
    score = CharField(null=True)
    tournament = CharField(null=True)
    source = CharField()
    fetched_at = DateTimeField()


class TennisExplorerPlayerSlug(HistoricalBaseModel):
    class Meta:
        table_name = "tennisexplorer_player_slug"
        indexes = ((("surname_key", "slug"), True),)

    surname_key = CharField()
    slug = CharField()
    discovered_at = DateTimeField()


class TennisHeadToHead(HistoricalBaseModel):
    class Meta:
        table_name = "tennis_head_to_head"
        indexes = ((("player_a_name", "player_b_name"), False),)

    player_a_name = CharField()
    player_b_name = CharField()
    a_wins = IntegerField()
    b_wins = IntegerField()
    n_matches = IntegerField()
    computed_at = DateTimeField()


class TennisRecentResult(HistoricalBaseModel):
    class Meta:
        table_name = "tennis_recent_result"
        indexes = ((("player_name", "match_date", "opponent_name"), True),)

    player_name = CharField()
    match_date = CharField()
    tournament = CharField(null=True)
    opponent_name = CharField()
    subject_won = BooleanField()
    retired_or_walkover = BooleanField()
    round = CharField(null=True)
    fetched_at = DateTimeField()


class TennisAbstractElo(HistoricalBaseModel):
    class Meta:
        table_name = "tennis_abstract_elo"
        indexes = ((("player_name", "tour"), False),)

    player_name = CharField()
    tour = CharField()
    elo = FloatField()
    h_elo = FloatField(null=True)
    c_elo = FloatField(null=True)
    g_elo = FloatField(null=True)
    atp_rank = IntegerField(null=True)
    fetched_at = DateTimeField()


class TennisAbstractYelo(HistoricalBaseModel):
    class Meta:
        table_name = "tennis_abstract_yelo"
        indexes = ((("player_name", "tour"), False),)

    player_name = CharField()
    tour = CharField()
    yelo = FloatField()
    wins = IntegerField(null=True)
    losses = IntegerField(null=True)
    fetched_at = DateTimeField()


class TennisAbstractCareerSplit(HistoricalBaseModel):
    class Meta:
        table_name = "tennis_abstract_career_split"
        indexes = ((("player_name", "surface"), False),)

    player_name = CharField()
    surface = CharField()
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


class TournamentLocationCache(HistoricalBaseModel):
    class Meta:
        table_name = "tournament_location_cache"
        indexes = ((("city_candidate",), True),)

    city_candidate = CharField()
    lat = FloatField(null=True)
    lon = FloatField(null=True)
    source = CharField()
    fetched_at = DateTimeField()


class WtaPlayerRankingHistory(HistoricalBaseModel):
    class Meta:
        table_name = "wta_player_ranking_history"
        indexes = ((("player_id", "ranked_at"), True),)

    player_id = IntegerField()
    player_name = CharField()
    ranked_at = CharField()
    singles_ranking = IntegerField()
    fetched_at = DateTimeField()


class WtaTournamentLevel(HistoricalBaseModel):
    class Meta:
        table_name = "wta_tournament_level"
        indexes = ((("year", "tournament_name"), True),)

    year = IntegerField()
    tournament_name = CharField()
    level = CharField(null=True)
    fetched_at = DateTimeField()
