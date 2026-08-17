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
una migración real de datos, no se hace a ciegas).

2026-08-11, pedido explícito del usuario ("reordenaremos la estructura
de los modelos de base") -- las clases de este archivo están ordenadas
por DEPENDENCIA real (quien no depende de nadie primero: `Player`,
`Tournament`, `Match`; recién después lo que los referencia por FK).
Motivo real, no solo prolijidad: un `ForeignKeyField` necesita la
clase Python YA definida al momento en que Peewee bindea el field --
production tuvo un crash real (`AttributeError: 'str' object has no
attribute '_meta'`) por un `ForeignKeyField("Tournament", ...)` con la
clase pasada como STRING crudo (no `DeferredForeignKey`, que sí
resuelve forward-references) definida 300 líneas más abajo. Con el
orden correcto, CERO forward-references hacen falta en todo el
archivo -- si se agrega un modelo nuevo, agregarlo DESPUÉS de todo lo
que referencia por FK, nunca antes."""

from peewee import (
    BooleanField, CharField, DateField, DateTimeField, FloatField,
    DeferredForeignKey, ForeignKeyField, IntegerField, TextField,
)

from database.base_models import HistoricalBaseModel


class Player(HistoricalBaseModel):
    """Entidad canónica de jugador/a de tenis -- 2026-08-10, pedido
    explícito del usuario tras la saga de bugs de esta sesión
    (triplicación de `MatchHistory`, contaminación ATP/WTA en `tour`,
    misattribution Casper/Christian Ruud): "los datos de player deben
    ir en el modelo de player, y simplemente referenciar a los players
    en los matches" -- `MatchHistory` no tenía ninguna FK a una entidad
    de jugador, todo se identificaba por STRING de nombre libre
    (`player_a_name`/`player_b_name`), causa raíz estructural de toda
    esa clase de bugs (2 formatos de nombre distintos para la misma
    persona real nunca se reconocían como la misma fila).

    MIGRACIÓN EN 2 FASES (deliberado, sin romper nada en producción):
    Fase 1 (esta clase + `player_a_id`/`player_b_id` en `MatchHistory`,
    ver abajo): agrega la tabla y las columnas FK SIN tocar ni borrar
    los campos de texto libre existentes (`player_a_name`, etc.) -- los
    puntos de escritura (`save_match_detail`/`save_match_history`/
    `save_next_match`) empiezan a resolver-o-crear `Player` y llenar el
    FK, en paralelo al nombre de texto (dual-write, transición segura).
    Fase 2 (NO implementada todavía, requiere aprobación aparte):
    migrar los ~20 scripts que hoy leen `player_a_name`/`player_b_name`
    como string a leer por FK/join, y recién ahí eliminar los campos de
    texto libre viejos.

    `tour` acá es la fuente de verdad REAL por jugador (una sola vez,
    nunca por partido) -- una vez que `MatchHistory` empiece a
    referenciar `Player.tour` en vez de guardar su propio `tour` por
    fila, la clase entera de bug "mismo jugador con ATP en una fila y
    WTA en otra" deja de ser posible estructuralmente (sería 1 sola
    fila de `Player` con 1 solo `tour`)."""

    class Meta:
        # 2026-08-10, bug real encontrado desplegando en producción: YA
        # existía una tabla `player` huérfana en la DB (era, "nombre"/
        # "edad"/"sofascore_id"/etc., resto del proyecto PRE-PIVOT a
        # mercados de predicción, sin modelo actual en el código --
        # nunca se dropeó al migrar). `create_tables(safe=True)` usa
        # CREATE TABLE IF NOT EXISTS, así que silenciosamente NO creó
        # la tabla nueva -- la vieja quedó ahí con su esquema viejo,
        # `Player.canonical_name` no existía. Se usa `tennis_player`
        # (mismo prefijo que el resto de las tablas de este archivo,
        # ej. `tennis_player_bio`) para evitar la colisión -- la tabla
        # `player` vieja se deja INTACTA, sin tocar (dato huérfano, no
        # se borra sin decisión explícita aparte).
        table_name = "tennis_player"
        indexes = ((("canonical_name",), True),)

    # 2026-08-10: nombre COMPLETO real, en el formato nativo de
    # TennisExplorer ("Apellido(s) Nombre", ver `get_match_detail`) --
    # mismo criterio que ya usa `MatchHistory.player_a_name` hoy, para
    # que la migración de datos existentes (Fase 1) sea directa sin
    # tener que reordenar nada.
    canonical_name = CharField(unique=True)
    slug = CharField(null=True)  # slug real de TennisExplorer, si se conoce
    tour = CharField(null=True)  # "ATP" | "WTA" -- fuente de verdad REAL, 1 sola vez
    flashscore_participant_id = CharField(null=True, unique=True)
    flashscore_slug = CharField(null=True)
    flashscore_lookup_failed_at = DateTimeField(null=True)
    birthdate = DateField(null=True)
    height_cm = IntegerField(null=True)
    weight_kg = IntegerField(null=True)
    plays = CharField(null=True)
    created_at = DateTimeField()
    updated_at = DateTimeField(null=True)


class Tournament(HistoricalBaseModel):
    class Meta:
        table_name = "tournament"
        indexes = ((("year", "tournament_name"), True),)

    year = IntegerField()
    tournament_name = CharField()
    level = CharField(null=True)
    tour = CharField(null=True)  # "ATP" | "WTA" | "ATP/WTA"-- fuente de verdad REAL, 1 sola vez
    city = CharField()
    # 2026-08-16: país real de la sede, leído del `<h1>` de la página de
    # torneo de TennisExplorer ("Santiago 2026 (Chile)") -- ver
    # `tennisexplorer_tournament_scraper.py`. `null=True`: no todas las
    # filas lo tienen (challengers/ITF cuya página no se reconstruye).
    # Su razón de ser es hacer PRECISO el geocoding: "santiago" a secas
    # es ambiguo (Chile / Compostela / Cuba), "Santiago, Chile" no.
    country = CharField(null=True)
    lat = FloatField(null=True)
    lon = FloatField(null=True)
    fetched_at = DateTimeField()


class Match(HistoricalBaseModel):
    """Un registro por CADA partido detectado en un scan real.
    `market_id`/`ticker_a`/`ticker_b` para el tracking de Kalshi,
    `player_a/b_rank_at_match` con el ranking REAL al momento del
    partido (no el actual), `tournament`/`round` incluidos en el esquema,
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
    # 2026-08-08, pedido explícito del usuario -- worker nuevo
    # (`workers/tennis_match_history_sync_worker.py`) que sincroniza
    # MatchHistory/ranking/stats para los jugadores de cada partido
    # nuevo detectado acá. NULL = todavía no procesado por ese worker.
    history_synced_at = DateTimeField(null=True)
    # Relación explícita mercado -> partido deportivo. Es nullable
    # durante el instante previo a resolver/crear la fila canónica.
    match_history = DeferredForeignKey(
        "MatchHistory", backref="kalshi_scans", null=True, on_delete="SET NULL",
    )


class MatchDuration(HistoricalBaseModel):
    """Duración REAL de un partido (total + por set) -- 2026-08-12,
    pedido explícito del usuario: "necesitamos la duración de los
    matches... añadir un modelo para esto MatchDuration y campo a la
    tabla de matches con foreignkey a esta nueva tabla".

    FUENTE: Flashscore, feed `df_su_1` (Summary) -- TennisExplorer NO
    expone duración en ninguna parte (verificado exhaustivamente en vivo
    2026-08-12 contra un partido real ya backfillado, Sinner-Alcaraz
    Masters Cup 2025: sin campo "Duration", sin patrón H:MM oculto en
    toda la página). Reverse-engineered 2026-08-10, re-verificado en
    vivo 2026-08-12: `RB÷{total}` + `RC÷{set1}`/`RD÷{set2}`/`RE÷{set3}`/
    `RF÷{set4}`/`RG÷{set5}`, formato "H:MM" -- confirmado con un partido
    real que la suma de sets (0:59+0:53+1:03=175min) coincide EXACTO con
    el total reportado (RB÷2:55=175min).

    `flashscore_match_id`: identificador real para trazabilidad/
    idempotencia -- NO se resuelve solo (requiere `find_match_id`,
    matching por nombre+fecha+marcador, ver `flashscore_scraper.py`),
    se guarda para poder re-verificar/depurar sin tener que re-resolver
    el matching."""

    class Meta:
        table_name = "tennis_match_duration"
        indexes = ((("flashscore_match_id",), True),)

    flashscore_match_id = CharField(unique=True)
    total_minutes = IntegerField(null=True)
    set_1_minutes = IntegerField(null=True)
    set_2_minutes = IntegerField(null=True)
    set_3_minutes = IntegerField(null=True)
    set_4_minutes = IntegerField(null=True)
    set_5_minutes = IntegerField(null=True)
    fetched_at = DateTimeField()


class MatchHistory(HistoricalBaseModel):
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
        table_name = "match_history"
        indexes = (
            (("match_id",), True),
            (("player_a", "match_date"), False),
            (("player_b", "match_date"), False),
            (("player_a", "player_b", "match_date"), False),
            (("surface", "match_date"), False),
        )

    match_id = CharField(unique=True)
    # 2026-08-10, pedido explícito del usuario ("usaremos sólo
    # tennisexplorer") -- única fuente real ahora es TennisExplorer.
    source = CharField(default="tennisexplorer")  # "tennisexplorer"
    # Nullable + fail-closed: None si no se pudo determinar con certeza
    # (nunca se adivina "ATP por default").
    tour = CharField(null=True)  # "ATP" | "WTA" | None
    match_date = CharField(null=True)
    # 2026-08-11, pedido explícito del usuario ("añade la key a
    # tournament, para que quede relacional") -- FK real contra
    # `Tournament` (ya definida ANTES en este archivo tras el
    # reordenamiento por dependencia del mismo día, ver docstring del
    # módulo). Revertido de un CharField anterior (2026-08-10) que se
    # había elegido por miedo a romper decenas de archivos que lo usaban
    # como string -- ese riesgo ya se resolvió acá mismo actualizando
    # cada consumidor real (`writer.py::_resolve_tournament_id`,
    # backtests). `null=True` -- filas viejas/sin torneo detectable
    # quedan sin FK, nunca se fabrica un `Tournament` fantasma.
    tournament = ForeignKeyField(Tournament, backref="matches", null=True, on_delete="SET NULL")
    round = CharField(null=True)
    surface = CharField(null=True)
    score = CharField(null=True)

    # 2026-08-10, pedido explícito del usuario ("los datos de player
    # deben ir en el modelo de player, y simplemente referenciar a los
    # players en los matches") -- Fase 1 de la migración a entidad
    # canónica `Player`: FK nuevas, EN PARALELO a los campos de texto
    # libre de arriba (no se tocan todavía, ver docstring de `Player`).
    # `null=True` a propósito -- filas viejas quedan sin FK hasta que la
    # migración retroactiva (`scripts/tennis/migrate_players_to_canonical_entity.py`)
    # las procese; filas nuevas se llenan en el momento vía
    # `writer.py::_resolve_player_id`. Nombrado `player_a`/`player_b`
    # (NO `player_a_id`) a propósito -- convención real de Peewee: el
    # campo FK ya genera solo la columna `player_a_id` + el atributo
    # `.player_a_id` (acceso al id crudo sin cargar la fila) -- nombrar
    # el campo Python `player_a_id` directamente generaría una columna
    # `player_a_id_id`, confuso y no lo que se pidió.
    player_a = ForeignKeyField(Player, backref="matches_as_a", null=True, on_delete="SET NULL", column_name="player_a_id")
    player_b = ForeignKeyField(Player, backref="matches_as_b", null=True, on_delete="SET NULL", column_name="player_b_id")
    player_a_ranking = IntegerField(null=True)  # ranking AL MOMENTO de este partido -- se queda acá, es del partido, no del jugador
    player_b_ranking = IntegerField(null=True)

    # 2026-08-11, pedido explícito del usuario ("sigamos con el Elo
    # propio en base al historial de matches") -- Elo de cada jugador
    # ANTES de este partido (mismo criterio que player_a_ranking/
    # player_b_ranking: snapshot AL MOMENTO, no el Elo actual del
    # jugador). Se completa en un pase batch por
    # `scripts/tennis/compute_self_elo.py` (recorre MatchHistory en
    # orden cronológico por tour, ver ese script y
    # `models/volatility/sports/tennis/tennis_elo.py` para la fórmula),
    # NO en el momento de escritura del partido -- Elo point-in-time
    # requiere procesar TODO el historial anterior del jugador en orden,
    # no se puede calcular fila por fila de forma aislada.
    player_a_elo = FloatField(null=True)
    player_b_elo = FloatField(null=True)

    # 2026-08-11, pedido explícito del usuario ("podríamos sacar ambas,
    # Elo y ranking por el historial de matches") -- ranking PROPIO
    # (posición 1/2/3/... ordenando por Elo descendente), no el ranking
    # oficial ATP/WTA (sistema de puntos, no relacionado matemáticamente
    # con Elo, ver docstring de player_a_elo/tennis_elo.py). Honesto:
    # incluye a TODO jugador visto hasta ese momento en el historial del
    # tour, no solo "activos" -- el ranking oficial descarta jugadores
    # tras 52 semanas sin competir, esto no hace esa distinción.
    player_a_elo_rank = IntegerField(null=True)
    player_b_elo_rank = IntegerField(null=True)
    # Weighted Elo point-in-time: rating general con decaimiento temporal
    # y margen de sets, más rating específico de la superficie.
    player_a_weighted_elo = FloatField(null=True)
    player_b_weighted_elo = FloatField(null=True)
    player_a_surface_elo = FloatField(null=True)
    player_b_surface_elo = FloatField(null=True)

    # 2026-08-12, pedido explícito del usuario ("necesitamos la duración
    # de los matches... añadir un modelo... y campo a la tabla de
    # matches con foreignkey a esta nueva tabla") -- se completa vía un
    # pipeline de ENRIQUECIMIENTO POSTERIOR (`scripts/tennis/
    # enrich_match_durations.py`), NO en el momento de escritura del
    # partido -- requiere resolver el `match_id` de Flashscore (fuente
    # real de la duración, TennisExplorer no la expone) vía matching por
    # nombre+fecha+marcador, un costo de red separado del backfill
    # principal. `null=True` -- la inmensa mayoría de `MatchHistory`
    # nunca tendrá esto completado (no bloqueante, ver docstring del
    # script de enriquecimiento).
    duration = ForeignKeyField(MatchDuration, backref="matches", null=True, on_delete="SET NULL")

    # 2026-08-10, pedido explícito del usuario ("si winnerside pero
    # referenciado a player") -- reemplaza al viejo `winner_side`
    # ("a"|"b", texto libre contra player_a/player_b) por un FK real
    # al `Player` ganador. `null=True` -- partidos sin resultado
    # determinable (walkover sin datos, o todavía sin jugar) quedan sin
    # ganador, nunca se adivina.
    winner = ForeignKeyField(Player, backref="matches_won", null=True, on_delete="SET NULL", column_name="winner_id")

    # 2026-08-09: `winner_age`/`loser_age`/`minutes`/`best_of` -- columnas
    # históricas, sin productor activo desde la purga de la fuente externa
    # que las traía (2026-08-10, "usaremos sólo tennisexplorer").
    # `winner_age`/`loser_age`/`minutes` van atados a GANADOR/PERDEDOR
    # (no a player_a/player_b). `best_of` es simétrico (no depende de
    # quién ganó).
    winner_age = FloatField(null=True)
    loser_age = FloatField(null=True)

    # `player_retired` -- RESTAURADO 2026-08-11, pedido explícito del
    # usuario ("hay que agregar los retiros de los players"). La fuente
    # primaria es la tabla Injuries del perfil de TennisExplorer, enlazada
    # de forma estricta por jugador+fecha+torneo+match_id. Los walkovers
    # no se marcan como retiros. Un "ret." explícito en match-detail se
    # conserva como fallback compatible.
    retired = BooleanField(default=False)
    player_retired = ForeignKeyField(Player, backref="matches_retired", null=True, on_delete="SET NULL", column_name="player_retired_id")
    retirement_reason = CharField(null=True)
    retirement_score = CharField(null=True)
    injury_end_date = CharField(null=True)
    retirement_source = CharField(null=True)

    fetched_at = DateTimeField()
    updated_at = DateTimeField(null=True)  # solo se setea si una re-verificación cambió algún campo


class TennisPlayerStrength(HistoricalBaseModel):
    """Último Weighted Elo disponible para decisiones pre-match."""

    class Meta:
        table_name = "tennis_player_strength"
        indexes = ((("player", "tour"), True),)

    player = ForeignKeyField(Player, backref="strengths", on_delete="CASCADE")
    tour = CharField()
    overall_elo = FloatField()
    hard_elo = FloatField(null=True)
    clay_elo = FloatField(null=True)
    grass_elo = FloatField(null=True)
    last_match_date = DateField(null=True)
    updated_at = DateTimeField()


class TennisEloCalibration(HistoricalBaseModel):
    """Platt scaling por tour/superficie para convertir Elo en probabilidad."""

    class Meta:
        table_name = "tennis_elo_calibration"
        indexes = ((("tour", "surface"), True),)

    tour = CharField()
    surface = CharField()
    intercept = FloatField(default=0.0)
    slope = FloatField(default=1.0)
    sample_count = IntegerField(default=0)
    fitted_through = DateField(null=True)
    updated_at = DateTimeField()


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
        indexes = ((("match", "phase"), True), (("match_history", "phase"), True))

    match = ForeignKeyField(Match, backref="weather", on_delete="CASCADE", null=True)
    # 2026-08-09, pedido explícito del usuario -- "que esté la
    # referencia a clima del match, ya que tenemos una tabla clima":
    # mismo patrón que `MatchStats.match_history` (ver ese modelo) --
    # `match` (arriba) sigue apuntando a `Match` (tracker vivo de
    # Kalshi, Categoría B) por retrocompatibilidad de las 233 filas
    # reales ya vinculadas así; `match_history` es el vínculo NUEVO
    # para partidos con identidad ya cierta en `MatchHistory`. Ambos
    # nullable -- una fila real usa uno u otro, nunca los 2 a la vez
    # hoy (podría cambiar si a futuro se resuelve el vínculo Match<->
    # MatchHistory para los partidos vivos de Kalshi).
    match_history = ForeignKeyField(MatchHistory, backref="weather", on_delete="CASCADE", null=True)
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
        indexes = ((("match_history",), True),)
    # 2026-08-08, pedido explícito del usuario -- fusión de `ChartedMatchStats`
    # acá: `match` (arriba) sigue apuntando a `Match` (el tracker VIVO de
    # Kalshi, Categoría B, fuera de la consolidación) por retrocompatibilidad
    # de las 66 filas reales ya vinculadas así -- NO se re-apunta (no hay
    # vínculo cierto entre esas 66 filas y `MatchHistory` sin el mismo
    # matching fuzzy que se viene evitando toda la sesión). `match_history`
    # es el vínculo NUEVO, para partidos con identidad ya cierta (Charting
    # Project migrado, y a futuro el worker de Kalshi que cree la fila de
    # `MatchHistory` en el momento con el nombre real del ticker -- ver
    # docstring de ese worker cuando exista).
    match_history = ForeignKeyField(MatchHistory, backref="stats", on_delete="CASCADE", unique=True, null=True)
    source = CharField(default="flashscore")  # "flashscore"
    # Backwards-compatible identifier fields used by legacy code/tests
    market_id = CharField(null=True, unique=True)
    flashscore_match_id = CharField(null=True)
    # Orientación del feed crudo: True si MatchHistory.player_a era P1.
    # Las columnas tipadas/raw se guardan siempre en orientación canónica A/B.
    player_a_is_flashscore_p1 = BooleanField(null=True)
    raw_stats_json = TextField()
    fetched_at = DateTimeField()
    is_usable = BooleanField(default=True)
    invalid_reason = TextField(null=True)

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
        indexes = ((("match_history", "set_number"), True),)
    # 2026-08-09, pedido explícito del usuario -- mismo patrón que
    # `MatchStats.match_history`/`MatchWeather.match_history`: `match`
    # (arriba) sigue apuntando a `Match` (tracker vivo de Kalshi,
    # retrocompatibilidad) por si alguna fila real ya está vinculada
    # así; `match_history` es el vínculo NUEVO para partidos con
    # identidad ya cierta en `MatchHistory` -- da `match.set_stats`
    # (queryset de sets de ESE partido, ya filtrado por el FK, sin
    # necesidad de re-filtrar por match del lado del caller).
    match_history = ForeignKeyField(MatchHistory, backref="set_stats", on_delete="CASCADE", null=True)
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


class TennisMatchHistoryUnvalidated(HistoricalBaseModel):
    """Candidatos de partido único que NO se pudieron confirmar contra
    una fuente histórica externa -- 2026-08-08, pedido explícito del
    usuario: "los que no se puedan validar los dejamos en una tabla
    aparte" (nunca se descartan, nunca se fuerza una identidad sin
    confirmar). 2026-08-10: la fuente externa de validación fue
    eliminada del proyecto -- tabla/infra de validación ya sin
    productor activo, queda documentada por si se retoma con
    TennisExplorer como única fuente.

    `reason` explica por qué (valores históricos, la infra que los
    generaba ya no corre): "dedup_conflict" (ambiguo desde el dedup
    mismo).

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

# `ChartedMatch`/`ChartedMatchStats` (Match Charting Project import) --
# REMOVIDOS 2026-08-09, pedido explícito del usuario ("el único modelo
# válido para matches es MatchHistory, borrar los otros"): su data (7,744
# partidos + 3,783 stats) ya se había fusionado en `MatchHistory`/
# `MatchStats` en una migración previa de esta misma sesión, y el script
# que los poblaba (`import_match_charting_project.py`) ya se había
# borrado -- cero consumidores vivos al momento de eliminar las clases.


# `TennisMatchHistory` -- ELIMINADA 2026-08-09, pedido explícito del
# usuario ("el modelo TennisMatchHistory... es redundante, eliminarlo y
# los datos en la tabla moverlos a MatchHistory"). 1 fila por JUGADOR
# por partido (`player_name`/`opponent_name`), redundante con
# `MatchHistory` (1 fila por partido real). Su data (tabla en vivo +
# backup `tennis_match_history_backup_20260809`, 447,513 filas reales
# combinadas) se migró a `MatchHistory` vía merge idempotente por
# (par de jugadores + fecha) antes de borrar esta clase -- ver memoria
# de sesión para el detalle exacto del merge. Su único consumidor real
# de código (`wta_favorite_surface_winrate_filter_backtest.py`) fue
# migrado a `MatchHistory` en el mismo cambio, eliminando además la
# maquinaria de resolución de nombre abreviado (`_build_abbrev_to_full_
# name_index`/`_resolve_abbreviated_name`) que solo existía por la
# limitación de este modelo viejo (opponent_name siempre abreviado).


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
    match_history = ForeignKeyField(MatchHistory, backref="historical_odds", null=True, on_delete="CASCADE")


# `TmlDatabaseMatch` -- ELIMINADA 2026-08-09. 2026-08-10: la fuente
# externa que la alimentaba fue purgada por completo del proyecto
# ("usaremos sólo tennisexplorer") -- `winner_age`/
# `loser_age`/`minutes`/`best_of`/`level` quedan en `MatchHistory` como
# columnas históricas, sin productor activo hasta que exista un
# equivalente vía TennisExplorer.


class TennisPlayerSyncStatus(HistoricalBaseModel):
    """Estado de sincronización de `MatchHistory`/ranking/stats POR
    JUGADOR (no por partido) -- 2026-08-09, pedido explícito del
    usuario: "el engine de scan sólo debe usar la data de la base...
    debe haber una columna/campo de última actualización... se podrá
    lanzar una orden al worker... indicarle prioridad".

    Cola de prioridad simple vía DB (sin message broker en este stack,
    ambos procesos son contenedores separados que solo comparten la
    base): `priority_requested_at` lo setea el ENGINE de scan
    (`engine.py`) cuando detecta un jugador nuevo y necesita su data
    actualizada YA, antes de decidir con él. El worker
    (`workers/tennis_match_history_sync_worker.py`) revisa esta tabla
    PRIMERO en cada ciclo (antes de su barrido normal por partido
    viejo), procesa esos jugadores con prioridad, y marca
    `priority_processed_at` al terminar. `match_history_updated_at`/
    `stats_checked_at` se actualizan en CADA sync real (prioritario o
    no) -- son lo que el engine consulta para decidir si la data está
    lo bastante fresca para confiar en ella, SIN fetchear nada él
    mismo (fail-closed: si nunca se sincronizó, o hay un pedido de
    prioridad todavía sin procesar, el engine debe tratar a ese
    jugador como "no listo" y saltarlo ese ciclo, no adivinar)."""

    class Meta:
        table_name = "tennis_player_sync_status"
        indexes = ((("player_name",), True),)

    player_name = CharField(unique=True)
    match_history_updated_at = DateTimeField(null=True)
    stats_checked_at = DateTimeField(null=True)
    priority_requested_at = DateTimeField(null=True)
    priority_processed_at = DateTimeField(null=True)


class TennisPlayerRanking(HistoricalBaseModel):
    class Meta:
        table_name = "tennis_player_ranking"
        indexes = ((("player_name", "tour"), False),)

    player_name = CharField()
    tour = CharField()
    ranking = IntegerField()
    country = CharField(null=True)
    fetched_at = DateTimeField()
    player = ForeignKeyField(Player, backref="official_rankings", null=True, on_delete="CASCADE")


# `TennisServeCalibration` -- ELIMINADA 2026-08-10, pedido explícito del
# usuario tras auditoría de writer.py. Sin producer real desde la purga
# de Sackmann/Match Charting Project (su único consumidor histórico,
# `outer_apis/tennis_serve_stats.py`, ya no existe); `save_serve_
# calibration_snapshot` en writer.py tenía cero callers reales en todo
# el proyecto. Reemplazo propuesto (agregados propios por jugador desde
# stats reales de partido) sigue como decisión de diseño abierta, no
# implementado.


# `TennisSurfaceMatchHistory` -- ELIMINADA 2026-08-09, pedido explícito
# del usuario. 1 fila por JUGADOR por partido (con superficie real de
# TennisExplorer), redundante con `MatchHistory` (que ya tiene su
# propio campo `surface`). Sin consumidores de código reales al momento
# de eliminarla (`save_surface_match_history`/`get_recent_surface_
# match_history`/`get_all_surface_matches` en `writer.py` ya estaban
# migradas a `MatchHistory` de una fase anterior de esta misma sesión).


class TennisAbstractElo(HistoricalBaseModel):
    class Meta:
        table_name = "tennis_abstract_elo"
        # 2026-08-10, bug real encontrado corriendo tests: el índice
        # apuntaba a "player_name"/"tour", 2 campos que YA NO EXISTEN
        # en este modelo (reemplazados por el FK `player` cuando se
        # migró a la entidad canónica `Player`, sin actualizar el
        # índice) -- rompía `create_tables` para TODA la DB (Peewee
        # valida los índices de todas las tablas al crear el schema).
        indexes = ((("player",), False),)

    player = ForeignKeyField(Player, backref="elo_history", on_delete="CASCADE")
    elo = FloatField()
    h_elo = FloatField(null=True)
    c_elo = FloatField(null=True)
    g_elo = FloatField(null=True)
    rank = IntegerField(null=True)
    fetched_at = DateTimeField()


class PlayerStats(HistoricalBaseModel):
    class Meta:
        table_name = "PlayerStats"
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
    player = ForeignKeyField(Player, backref="career_surface_stats", null=True, on_delete="CASCADE")


class PlayerRankingHistory(HistoricalBaseModel):
    class Meta:
        table_name = "wta_player_ranking_history"
        indexes = ((("player_id", "ranked_at"), True),)

    player = ForeignKeyField(Player, backref="ranking_history", on_delete="CASCADE")
    ranked_at = CharField()
    singles_ranking = IntegerField()
    fetched_at = DateTimeField()


class TennisPlayerWikidataReference(HistoricalBaseModel):
    """Base de verdad REAL para resolver definitivamente el orden
    nombre/apellido de jugadores de tenis -- 2026-08-10, pedido
    explícito del usuario tras la saga de duplicados/reordenes de
    `Player.canonical_name` ("busca una lista en wikipedia con nombres
    de tenistas de atp y wta... eso dejalo como base para que después
    no se armen enredos").

    Poblado vía SPARQL contra Wikidata (`build_tennis_player_wikidata_
    reference.py`) -- NO scraping de HTML de listas de Wikipedia
    (fràgil, mal estructurado); Wikidata YA tiene `P735` (nombre de
    pila) y `P734` (apellido) como propiedades ESTRUCTURADAS separadas
    para cada persona con ocupación "tennis player" (`Q10833314`) --
    elimina la ambigüedad de raíz en vez de heurística de reordenar
    texto libre (`_te_native_to_nombre_apellido`, que SOLO puede
    adivinar, nunca confirmar).

    `canonical_name` = f"{given_name} {family_name}" (SIEMPRE
    "Nombre Apellido", nunca ambiguo -- viene de 2 campos separados,
    no de un string a reordenar). Nombres sin AMBOS `P735`/`P734`
    presentes en Wikidata se excluyen (fail-closed, mejor no tener
    entrada que tener una a medias)."""

    class Meta:
        table_name = "tennis_player_wikidata_reference"
        indexes = ((("wikidata_id",), True),)

    wikidata_id = CharField()  # ej. "Q38129" (URI corto, sin el prefijo)
    canonical_name = CharField()  # "Nombre Apellido", SIEMPRE, ver docstring
    given_name = CharField()
    family_name = CharField()
    fetched_at = DateTimeField()
