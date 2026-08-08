"""Consolidated historical models for matches, players, rankings,
probability ticks and match weather.

Este módulo introduce tablas nuevas y no borra las existentes. Sirve
como paso intermedio para unificar lecturas/escrituras a futuro.

2026-08-08, revisión post-consolidación (pedido explícito del usuario:
"revisa, quizás hay que modificar tablas y realizar migraciones de
data") -- se encontraron y arreglaron 2 problemas reales en el intento
original de consolidación (hecho con Copilot, sin commitear):

1. `Match` se usaba como target de `ForeignKeyField` en `ProbabilityTick`/
   `MatchWeather`/`MatchStatsSet` pero nunca se definía en este archivo
   (vivía, duplicado, en un archivo paralelo `database/historical/tennis/
   tennismatch.py` que nada importaba) -- rompía 45 tests
   (`NameError: Match`). Fix: `Match` ahora se define ACÁ, como rename
   1:1 verificado de la real `TennisScannedMatch` (mismo `table_name`,
   mismos campos -- NINGUNA columna nueva, ver punto 3 abajo sobre por
   qué).

2. Existían `Player`("player")/`RankingHistory`("ranking_history") --
   2 tablas NUEVAS y vacías en producción -- y `database/historical/
   tennis/__init__.py` las aliaseaba como `Player as TennisPlayerBio`/
   `RankingHistory as TennisPlayerRanking`. Esto habría dejado
   INVISIBLES 91 filas reales de `tennis_player_bio` (fechas de
   nacimiento cacheadas) con solo deployar -- las 481,600 filas reales
   de `tennis_player_ranking` se salvaban por una coincidencia de orden
   de imports en Python (el import sin alias, listado después, pisaba
   al alias), un fusible frágil, no una protección real. Eliminadas
   `Player`/`RankingHistory` (0 uso real en todo el repo, reintroducían
   el patrón de FK/surrogate-key que este mismo proyecto ya documentó
   como explícitamente descartado en el rebuild de julio 2026 -- ver
   docstring de `database/historical/tennis/__init__.py`). Restaurada
   `TennisPlayerBio` acá mismo (antes vivía en su propio archivo,
   `tennisplayerbio.py`, borrado en el mismo intento de consolidación).

3. El `Match` original (en `tennismatch.py`) agregaba 5 columnas
   nuevas (`source`, `match_date`, `player_a_age_at_match`,
   `player_b_age_at_match`, `score`) que NO existen en la tabla real
   `tennis_scanned_match` (296 filas activas, usada AHORA MISMO por
   `workers/tennis_match_tick_worker.py` en producción) --
   `create_tables(safe=True)` nunca hace `ALTER TABLE` sobre una tabla
   ya existente, así que deployar esto tal cual hubiera roto cualquier
   lectura real de esa tabla (columna inexistente). Se sacaron esas 5
   columnas -- si en el futuro se necesitan de verdad, agregarlas
   requiere un `_add_column_if_missing` explícito en
   `database/db.py::init_db_historical`, igual que se hizo para
   `tournament`/`round` en su momento."""

from peewee import BooleanField, CharField, DateField, DateTimeField, FloatField, ForeignKeyField, IntegerField, TextField

from database.base_models import HistoricalBaseModel


class Match(HistoricalBaseModel):
    """Un registro por CADA partido de tenis detectado en un scan real
    (rename 1:1 de la real `TennisScannedMatch`, ver docstring del
    módulo arriba -- MISMO `table_name`, MISMOS campos, cero migración
    si se despliega así). Ver la entidad original para el detalle
    completo de cada campo -- `market_id`/`ticker_a`/`ticker_b` para el
    tracking de Kalshi, `player_a/b_rank_at_match` con el ranking REAL
    al momento del partido (no el actual), `tournament`/`round`
    agregados después vía ALTER TABLE (ver `_add_column_if_missing` en
    `database/db.py`), `winner`/`settled_at` NULL hasta que el mercado
    resuelva."""

    class Meta:
        table_name = "tennis_scanned_match"
        indexes = ((("market_id",), True),)

    market_id = CharField(unique=True)
    ticker_a = CharField()
    ticker_b = CharField()
    tour = CharField()
    surface = CharField(null=True)
    tournament = CharField(null=True)
    round = CharField(null=True)
    player_a_name = CharField()
    player_b_name = CharField()
    player_a_rank_at_match = IntegerField(null=True)
    player_b_rank_at_match = IntegerField(null=True)
    best_of = IntegerField()
    close_time = CharField(null=True)
    detected_at = DateTimeField()
    winner = CharField(null=True)
    settled_at = DateTimeField(null=True)


class TennisPlayerBio(HistoricalBaseModel):
    """Fecha de nacimiento real por jugador/a -- cache PERMANENTE
    (nunca cambia, se sirve para siempre sin volver a pedirla). Ver
    `outer_apis/scrapers/tennis/tennisabstract_player_scraper.py::
    get_date_of_birth`. Restaurada acá 2026-08-08 (ver docstring del
    módulo) -- antes vivía en su propio archivo, borrado por error en
    el intento de consolidación que la reemplazaba silenciosamente por
    una tabla nueva y vacía."""

    class Meta:
        table_name = "tennis_player_bio"
        indexes = ((("player_name",), True),)

    player_name = CharField(unique=True)
    date_of_birth = DateField()
    fetched_at = DateTimeField()


class ProbabilityTick(HistoricalBaseModel):
    class Meta:
        table_name = "tennis_match_probability_tick"
        indexes = ((("match", "observed_at"), False),)

    match = ForeignKeyField(Match, backref="ticks", on_delete="CASCADE")
    observed_at = DateTimeField()
    market_price_a = FloatField(null=True)
    model_fair_a = FloatField(null=True)


class MatchWeather(HistoricalBaseModel):
    class Meta:
        table_name = "tennis_match_weather"
        indexes = ((("match", "phase"), True),)

    match = ForeignKeyField(Match, backref="weather", on_delete="CASCADE")
    phase = CharField()  # before|during|end
    observed_at = DateTimeField()
    temperature_c = FloatField(null=True)
    humidity_pct = FloatField(null=True)
    wind_kmh = FloatField(null=True)
    fetched_at = DateTimeField(null=True)


class MatchStats(HistoricalBaseModel):
    """Raw and normalized match-level stats for tennis.

    `raw_stats_json` se conserva para el contenido original y para campos
    adicionales que aún no se normalicen. Los campos tipificados abajo son
    las métricas de tenis más habituales que se extraen de Flashscore
    para todo el partido.
    """

    class Meta:
        table_name = "tennis_match_stats"
        indexes = ((("match",), True),)

    match = ForeignKeyField(Match, backref="stats", on_delete="CASCADE", unique=True, null=True)
    # Backwards-compatible identifier fields used by legacy code/tests
    market_id = CharField(null=True, unique=True)
    flashscore_match_id = CharField(null=True)
    raw_stats_json = TextField()
    fetched_at = DateTimeField()

    # Match-level totals
    aces_a = IntegerField(null=True)
    aces_b = IntegerField(null=True)
    double_faults_a = IntegerField(null=True)
    double_faults_b = IntegerField(null=True)
    winners_a = IntegerField(null=True)
    winners_b = IntegerField(null=True)
    unforced_errors_a = IntegerField(null=True)
    unforced_errors_b = IntegerField(null=True)
    total_points_won_a = IntegerField(null=True)
    total_points_won_b = IntegerField(null=True)
    service_games_won_a = IntegerField(null=True)
    service_games_won_b = IntegerField(null=True)
    return_games_won_a = IntegerField(null=True)
    return_games_won_b = IntegerField(null=True)

    # Percentage stats
    first_serve_pct_a = FloatField(null=True)
    first_serve_pct_b = FloatField(null=True)
    second_serve_pct_a = FloatField(null=True)
    second_serve_pct_b = FloatField(null=True)
    first_serve_points_won_pct_a = FloatField(null=True)
    first_serve_points_won_pct_b = FloatField(null=True)
    second_serve_points_won_pct_a = FloatField(null=True)
    second_serve_points_won_pct_b = FloatField(null=True)
    break_points_won_a = IntegerField(null=True)
    break_points_won_b = IntegerField(null=True)
    break_points_faced_a = IntegerField(null=True)
    break_points_faced_b = IntegerField(null=True)
    break_points_saved_a = IntegerField(null=True)
    break_points_saved_b = IntegerField(null=True)
    serve_games_played_a = IntegerField(null=True)
    serve_games_played_b = IntegerField(null=True)


class MatchStatsSet(HistoricalBaseModel):
    """Set-level stats for tennis matches.

    Cada fila representa un set dentro de un match, con los mismos campos
    tipificados que en `MatchStats` pero para ese set individual.
    """

    class Meta:
        table_name = "tennis_match_stats_set"
        indexes = ((("match", "set_number"), True),)

    match = ForeignKeyField(Match, backref="set_stats", on_delete="CASCADE")
    set_number = IntegerField()
    raw_stats_json = TextField(null=True)
    fetched_at = DateTimeField(null=True)

    aces_a = IntegerField(null=True)
    aces_b = IntegerField(null=True)
    double_faults_a = IntegerField(null=True)
    double_faults_b = IntegerField(null=True)
    winners_a = IntegerField(null=True)
    winners_b = IntegerField(null=True)
    unforced_errors_a = IntegerField(null=True)
    unforced_errors_b = IntegerField(null=True)
    total_points_won_a = IntegerField(null=True)
    total_points_won_b = IntegerField(null=True)
    service_games_won_a = IntegerField(null=True)
    service_games_won_b = IntegerField(null=True)
    return_games_won_a = IntegerField(null=True)
    return_games_won_b = IntegerField(null=True)

    first_serve_pct_a = FloatField(null=True)
    first_serve_pct_b = FloatField(null=True)
    second_serve_pct_a = FloatField(null=True)
    second_serve_pct_b = FloatField(null=True)
    first_serve_points_won_pct_a = FloatField(null=True)
    first_serve_points_won_pct_b = FloatField(null=True)
    second_serve_points_won_pct_a = FloatField(null=True)
    second_serve_points_won_pct_b = FloatField(null=True)
    break_points_won_a = IntegerField(null=True)
    break_points_won_b = IntegerField(null=True)
    break_points_faced_a = IntegerField(null=True)
    break_points_faced_b = IntegerField(null=True)
    break_points_saved_a = IntegerField(null=True)
    break_points_saved_b = IntegerField(null=True)
    serve_games_played_a = IntegerField(null=True)
    serve_games_played_b = IntegerField(null=True)


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
