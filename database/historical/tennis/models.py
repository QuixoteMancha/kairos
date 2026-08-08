"""Modelos ESPECÍFICOS de tenis -- 2026-08-08, pedido explícito del
usuario: "definiendo modelos específicos en un solo file dentro de las
carpetas de deportes".

Todo lo que hay acá es SOLO tenis (ATP+WTA): match-tracking en vivo
(scan de Kalshi, ticks de precio, clima, stats de partido/set),
historial de partidos, cuotas, ranking, calibración de saque, Match
Charting Project, cache de slugs de TennisExplorer, ubicación de
torneos.

2026-08-08, corrección tras revisión del usuario ("dejaste un modelo
propio de tennis en models.py"): `Match`/`ProbabilityTick`/
`MatchWeather`/`MatchStats`/`MatchStatsSet` se habían dejado en
`database/historical/models.py` (el archivo de modelos BASE,
supuestamente genéricos/reusables entre deportes) razonando que su
ESTRUCTURA era agnóstica de deporte -- pero en la realidad su
`table_name` es literalmente tenis (`tennis_scanned_match`,
`tennis_match_probability_tick`, etc.) y HOY ningún otro deporte los
usa. Contradice la regla de "solo modelos base reales" en `models.py`
-- movidos acá. `database/historical/models.py` queda sin modelos
concretos hasta que exista un segundo deporte que de verdad comparta
esta estructura (en ese momento, decidir explícitamente si conviene
renombrar las tablas reales a algo sport-agnostic -- ESO SÍ requeriría
una migración real de datos, no se hace a ciegas)."""

from peewee import BooleanField, CharField, DateField, DateTimeField, FloatField, ForeignKeyField, IntegerField, TextField

from database.base_models import HistoricalBaseModel


class Match(HistoricalBaseModel):
    """Un registro por CADA partido detectado en un scan real.
    `market_id`/`ticker_a`/`ticker_b` para el tracking de Kalshi,
    `player_a/b_rank_at_match` con el ranking REAL al momento del
    partido (no el actual), `tournament`/`round` agregados después vía
    ALTER TABLE (ver `_add_column_if_missing` en `database/db.py`),
    `winner`/`settled_at` NULL hasta que el mercado resuelva."""

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
    """Raw and normalized match-level stats, vía Flashscore.
    `raw_stats_json` conserva el contenido original; los campos
    tipificados abajo son las métricas más habituales."""

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
    """Set-level stats -- una fila por set dentro de un `Match`, mismos
    campos tipificados que `MatchStats` pero para ese set individual."""

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


class TennisMatchDetail(HistoricalBaseModel):
    """Un registro por partido REAL (1 fila, NO duplicado por jugador
    como `TennisMatchHistory`) -- 2026-08-08, pedido explícito del
    usuario tras descubrir que la página `match-detail` de
    TennisExplorer resuelve identidad de AMBOS jugadores con CERTEZA
    (nombre completo real + slug ya desambiguado, ver
    `outer_apis/scrapers/tennis/tennisexplorer_player_scraper.py::
    get_match_detail`), evitando el matching fuzzy por apellido+fecha
    que causó los incidentes de misattribution de esta sesión.

    Trae además datos bio de ambos jugadores AL MOMENTO del partido
    (ranking, no snapshot actual como `TennisPlayerRanking`) y datos
    permanentes (fecha de nacimiento, altura, peso, mano dominante) que
    hoy no existen en ninguna otra tabla con este detalle.

    Idempotente por `match_id` (el ID real de TennisExplorer, cosechado
    gratis del link `/match-detail/?id=...` que ya viene en cada fila
    de historial, sin request extra para descubrirlo) -- ver
    `database/historical/tennis/writer.py::save_match_detail`: si el
    partido ya existe, NUNCA se duplica; solo se actualiza si algún
    campo cambió de verdad respecto a lo guardado (poco probable, pero
    posible por error/corrección de la fuente)."""

    class Meta:
        table_name = "tennis_match_detail"
        indexes = ((("match_id",), True),)

    match_id = CharField(unique=True)
    # 2026-08-08, pedido explícito del usuario -- "validaremos los
    # matches de tennisexplorer con la data de sackmann histórica para
    # validar la deduplicación": los partidos validados así NO tienen
    # `match_id` real de TennisExplorer (no hubo fetch de match-detail,
    # es validación contra `TmlDatabaseMatch`, sin red) -- usan un id
    # sintético determinístico (`sackmann:{year}:{winner}:{loser}`).
    # `source` distingue el origen real del dato para quien lo consuma.
    source = CharField(default="tennisexplorer")  # "tennisexplorer" | "sackmann"
    match_date = CharField(null=True)
    tournament = CharField(null=True)
    round = CharField(null=True)
    surface = CharField(null=True)
    score = CharField(null=True)
    winner_side = CharField(null=True)  # "a" | "b" | None si no determinable

    player_a_name = CharField()  # nombre completo real, ej. "Anisimova Amanda"
    player_a_slug = CharField(null=True)
    player_a_ranking = IntegerField(null=True)
    player_a_birthdate = DateField(null=True)
    player_a_height_cm = IntegerField(null=True)
    player_a_weight_kg = IntegerField(null=True)
    player_a_plays = CharField(null=True)

    player_b_name = CharField()
    player_b_slug = CharField(null=True)
    player_b_ranking = IntegerField(null=True)
    player_b_birthdate = DateField(null=True)
    player_b_height_cm = IntegerField(null=True)
    player_b_weight_kg = IntegerField(null=True)
    player_b_plays = CharField(null=True)

    fetched_at = DateTimeField()
    updated_at = DateTimeField(null=True)  # solo se setea si una re-verificación cambió algún campo


class TennisMatchHistoryUnvalidated(HistoricalBaseModel):
    """Candidatos de partido único (salidos de `dedupe_match_history_
    candidates.py`) que NO se pudieron confirmar contra `TmlDatabaseMatch`
    -- 2026-08-08, pedido explícito del usuario: "los que no se puedan
    validar los dejamos en una tabla aparte" (nunca se descartan, nunca
    se fuerza una identidad sin confirmar).

    `reason` explica por qué: "no_sackmann_match" (Sackmann no cubre
    ese nivel/torneo -- esperable, ITF/challenger bajo no siempre está),
    "ambiguous_sackmann_match" (2+ partidos candidatos el mismo año
    entre las mismas 2 personas, el score no alcanzó para desambiguar),
    "dedup_conflict" (ya venía ambiguo desde el dedup mismo, ver
    `find_match_candidates`).

    Idempotente por (match_date, tournament, player_a_name,
    player_b_name_or_abbrev) -- re-correr la validación no duplica."""

    class Meta:
        table_name = "tennis_match_history_unvalidated"
        indexes = ((("match_date", "tournament", "player_a_name", "player_b_name_or_abbrev"), True),)

    match_date = CharField(null=True)
    tournament = CharField(null=True)
    round = CharField(null=True)
    surface = CharField(null=True)
    score_from_a = CharField(null=True)
    player_a_name = CharField()
    # nombre completo si el dedup ya lo resolvió (candidato "paired"),
    # o la abreviación cruda si no ("singleton"/"conflict") -- por eso
    # el nombre genérico del campo, no "player_b_name".
    player_b_name_or_abbrev = CharField()
    player_b_is_full_name = BooleanField()
    reason = CharField()
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
