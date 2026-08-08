"""Modelos BASE de match-tracking + clima, reutilizables entre deportes
(hoy solo tenis los usa, pero la estructura es agnóstica de deporte) --
2026-08-08, pedido explícito del usuario: "dejar la base de modelos
para sports y clima en models.py... solo dejar modelos base para la
data de sports y clima, definiendo modelos específicos en un solo file
dentro de las carpetas de deportes".

Reemplaza a `match_models.py` (nombre anterior, ver historial de git
para el contexto completo de la consolidación 2026-08-08 -- bug real
de `Match` indefinido, alias peligrosos `Player`/`RankingHistory` que
hubieran ocultado datos reales, columnas nuevas sin `ALTER TABLE`).

QUÉ VIVE ACÁ (genérico, sport-agnostic en estructura):
  - `Match`: un partido/evento 1v1 trackeado (market_id de Kalshi,
    jugadores A/B, ranking al momento del partido, resultado). Hoy
    `table_name="tennis_scanned_match"` porque es la única tabla real
    que existe (296 filas reales en producción) -- el NOMBRE de tabla
    es histórico/tenis, pero el MODELO es reusable para cualquier
    deporte 1v1 futuro sin cambiar de forma.
  - `ProbabilityTick`: un tick de precio de mercado + fair price del
    modelo para un `Match`.
  - `MatchWeather`: clima real en una fase de un `Match` (before|
    during|end).
  - `MatchStats`/`MatchStatsSet`: estadísticas de partido/set para un
    `Match`, vía Flashscore.

QUÉ NO VIVE ACÁ: cualquier modelo específico de UN deporte (historial
de partidos de tenis, ranking, torneos, Match Charting Project, etc.)
-- eso vive en `database/historical/tennis/models.py` (un solo archivo
por carpeta de deporte, mismo criterio). `database/historical/tennis/
__init__.py` reexporta ambos grupos bajo los nombres `TennisScannedMatch`/
`TennisMatchProbabilityTick`/`TennisMatchWeather`/`TennisMatchStats`
(alias de los genéricos de acá) para no romper el código existente que
ya usaba esos nombres."""

from peewee import CharField, DateTimeField, FloatField, ForeignKeyField, IntegerField, TextField

from database.base_models import HistoricalBaseModel


class Match(HistoricalBaseModel):
    """Un registro por CADA partido detectado en un scan real (hoy solo
    tenis, ver docstring del módulo). `market_id`/`ticker_a`/`ticker_b`
    para el tracking de Kalshi, `player_a/b_rank_at_match` con el
    ranking REAL al momento del partido (no el actual), `tournament`/
    `round` agregados después vía ALTER TABLE (ver
    `_add_column_if_missing` en `database/db.py`), `winner`/
    `settled_at` NULL hasta que el mercado resuelva."""

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
    """Raw and normalized match-level stats (hoy solo tenis, vía
    Flashscore). `raw_stats_json` conserva el contenido original;
    los campos tipificados abajo son las métricas más habituales."""

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
