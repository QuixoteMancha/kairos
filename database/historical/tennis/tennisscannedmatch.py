from peewee import CharField, DateTimeField, FloatField, IntegerField, Model

from database.db import db_historical


class HistoricalBaseModel(Model):
    class Meta:
        database = db_historical


class TennisScannedMatch(HistoricalBaseModel):
    """Un registro por CADA partido de tenis detectado en un scan real,
    sin importar si la estrategia encontró edge o no (2026-07-31, pedido
    explícito del usuario -- a diferencia de `LimitArbSignal`, que solo
    loggea cuando `avg_volatility`/`return_opportunity` superan los
    umbrales mínimos y por lo tanto es una muestra sesgada).

    Guarda el ranking de cada jugador/a AL MOMENTO de este partido puntual
    (`player_a_rank_at_match`/`player_b_rank_at_match`), no el ranking
    actual -- cierra la limitación ya documentada en
    `outer_apis/sports/tennis/serve_stats.py` (Match Charting Project
    bucketiza por ranking ACTUAL de partidos históricos, no por el
    ranking real al momento de cada partido). Acá, como el registro se
    hace en tiempo real durante el scan, el ranking guardado SIEMPRE es
    el correcto para ese partido.

    `ticker_a`/`ticker_b`: tickers reales de Kalshi para cada pierna
    (necesarios para que el worker de ticks pueda refetchear el precio
    sin tener que reconstruir el mercado completo cada vez, ver
    workers/tennis_match_tick_worker.py).

    `winner`/`settled_at`: NULL hasta que el mercado resuelva -- llenado
    por el mismo worker de ticks al detectar `result` real de Kalshi.
    Fail-closed: nunca se adivina un ganador antes de la resolución real."""

    class Meta:
        table_name = "tennis_scanned_match"
        indexes = ((("market_id",), True),)  # único -- un partido se registra UNA sola vez

    market_id = CharField(unique=True)
    ticker_a = CharField()
    ticker_b = CharField()
    tour = CharField()  # "ATP" | "WTA"
    surface = CharField(null=True)  # None si el torneo no se reconoció, ver tournament_surface.py
    # Agregado 2026-08-01 (pedido explícito del usuario, para clima real
    # del partido, ver outer_apis/sports/tennis/match_weather.py). Columna
    # nueva sobre una tabla ya existente -- requiere ALTER TABLE manual
    # (Peewee's create_tables(safe=True) NO agrega columnas a tablas ya
    # creadas, ver database/db.py::init_db_historical docstring), no
    # alcanza con solo declararla acá. Nullable: filas de antes de este
    # cambio quedan con tournament=None, fail-closed (nunca se adivina el
    # torneo de una fila vieja).
    tournament = CharField(null=True)
    player_a_name = CharField()
    player_b_name = CharField()
    player_a_rank_at_match = IntegerField(null=True)
    player_b_rank_at_match = IntegerField(null=True)
    best_of = IntegerField()
    close_time = CharField(null=True)  # ISO string cruda de Kalshi, ver market['close_time']
    detected_at = DateTimeField()

    winner = CharField(null=True)  # 'a' | 'b' | None (no resuelto todavía)
    settled_at = DateTimeField(null=True)
