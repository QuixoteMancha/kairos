from peewee import CharField, DateField, DateTimeField, FloatField, IntegerField

from database.historical.tennis.tennisscannedmatch import HistoricalBaseModel


class ChartedMatch(HistoricalBaseModel):
    """Un partido real del Match Charting Project de Jeff Sackmann
    (`outer_apis/scrapers/tennis/match_charting_scraper.py`), importado
    2026-08-02 (pedido explícito del usuario: ampliar la base a ~10 años
    de historia real, complementando -- no duplicando -- lo que ya
    tenemos vía Kalshi/Flashscore en `TennisScannedMatch`).

    DOMINIO DISTINTO de `TennisScannedMatch`: estos partidos NUNCA
    tuvieron un mercado de Kalshi (son casi todos anteriores a que
    Kairos exista) -- sin `ticker_a`/`ticker_b`/precio, por diseño. Para
    los pocos casos donde SÍ hay solapamiento real (mismo jugador+fecha
    ya trackeado), no se crea una fila acá -- se enriquece la
    `TennisMatchStats` existente vía
    `database/historical/tennis/matchstats.py::merge_match_stats`.

    CAVEAT DE SESGO DE SELECCIÓN (documentado, no oculto): el Match
    Charting Project NO es una lista exhaustiva de todos los partidos
    del tour -- son los que un voluntario decidió cargar punto-por-punto,
    probablemente sesgados hacia partidos reñidos/famosos. Cualquier
    estadística agregada sobre esta tabla (ej. "% que gana el favorito")
    hereda ese sesgo, no representa el tour completo.

    `winner`: 'p1' | 'p2' (referido a `player_a_name`/`player_b_name` en
    ese orden) -- derivado del ÚLTIMO punto real jugado
    (`match_charting_scraper.derive_match_winners`), NUNCA de la
    convención "Player 1"/"Player 2" del CSV (que NO indica ganador, ver
    docstring de `get_matches_index`). Nullable: partidos cuyo ganador no
    se pudo derivar con confianza quedan `None`, nunca se adivina."""

    class Meta:
        table_name = "charted_match"
        indexes = ((("match_id",), True),)

    match_id = CharField(unique=True)  # ID real de Sackmann, ej. "20260521-M-Roland_Garros-Q3-..."
    tour = CharField()  # "ATP" | "WTA"
    player_a_name = CharField()  # "Player 1" del CSV (orden arbitrario, NO implica ganador)
    player_b_name = CharField()  # "Player 2"
    match_date = DateField()
    tournament = CharField(null=True)
    round = CharField(null=True)
    surface = CharField(null=True)
    best_of = IntegerField(null=True)
    winner = CharField(null=True)  # 'p1' | 'p2' | None

    # Clima (2026-08-02, pedido explícito del usuario -- ver
    # outer_apis/sports/tennis/match_weather.py::populate_weather_for_charted_match).
    # A DIFERENCIA de TennisMatchWeather (3 fases before/during/end, vía
    # ticks reales de Kalshi), acá solo hay UNA fila -- Sackmann no da
    # hora exacta de inicio (`Time` solo ~54% de los partidos) ni
    # duración real, así que "durante"/"al final" se fabricarían sin
    # base real. Un único snapshot representativo del día del partido
    # (14:00 UTC fijo, documentado como proxy, NO la hora real de cada
    # partido) es la única fase honesta que se puede poblar acá.
    temperature_c = FloatField(null=True)
    humidity_pct = FloatField(null=True)
    wind_kmh = FloatField(null=True)
    weather_fetched_at = DateTimeField(null=True)
