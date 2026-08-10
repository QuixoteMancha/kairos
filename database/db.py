"""
Capa de persistencia de Kairos — SQLite vía Peewee, DOS bases separadas
por propósito (esquema confirmado con el usuario, ver memoria
kairos-two-volume-db-scheme-2026-07-21):

  - `db` (contabilidad, `DB_PATH`): operativa del sistema — señales de
    LimitArb hoy, órdenes/fills/PnL más adelante. Vive en el volumen
    Docker "definitivo" (`kairos_data_v2` en la mini-pc).
  - `db_historical` (referencia/histórico, `DB_HISTORICAL_PATH`): datos
    de mercado/estadísticas históricas que alimentan calibración de
    modelos (ticks de Kalshi, velas de cripto, ratings de equipos, etc.).
    Vive en el volumen viejo (`kairos_kairos_data` en la mini-pc,
    rescatado de la arquitectura pre-"extreme makeover" — ver
    database/historical/).

Estructura del paquete `database/` (carpetas por tema, mismo patrón que
`models/volatility/sports/` y `models/fairprice/sports/`):
    database/
    ├── db.py                       (este archivo — conexiones + init)
    ├── base_models.py              (BaseModel/HistoricalBaseModel/ArchiveBaseModel compartidos)
    ├── accounting/                 (db, DB_PATH — operativo)
    │   ├── limitarbsignal.py
    │   └── writer.py
    └── historical/                 (db_historical, DB_HISTORICAL_PATH — referencia)
        ├── models.py               (2026-08-08: modelos BASE genuinamente reusables
        │                            entre 2+ deportes -- HOY VACÍO de modelos
        │                            concretos, ver su docstring)
        ├── market_data/            (KalshiMarketTick, CryptoPriceSnapshot — rescatadas)
        ├── team_sports/            (TeamRating, TeamMatchHistory, TeamHeadToHead + writer.py)
        └── tennis/                 (models.py: TODO lo específico de tenis en un solo
                                      archivo -- incluye Match/ProbabilityTick/
                                      MatchWeather/MatchStats (100% tenis en la
                                      práctica hoy, ver su docstring); writer.py)

Rescatado (patrón, no el código completo) de la arquitectura pre-"extreme
makeover" (`src/database/database.py`, borrado en `02d7f35`): mismo motor
(SQLite + WAL) para ambas bases, mismo mecanismo de ruta configurable vía
env var. El resto del schema viejo (Player/Wallet/BetMarket/Event/... —
decenas de modelos Peewee de la arquitectura multi-plataforma con paper
trading que ya no existe) NO se rescata salvo las tablas con datos reales
(ver database/historical/market_data/ y team_sports/teamrating.py) — el
resto estaban vacías, confirmado por inspección directa.

IMPORTANTE: esto deja el schema y la conexión listos, pero NO hay todavía
ningún worker/collector que escriba datos nuevos en `db_historical` — la
población activa de esas tablas (ej. un tick collector de Kalshi) es
trabajo futuro separado, no incluido acá.

Rutas de las DBs:
  - `DB_PATH`/`DB_HISTORICAL_PATH` en `.env` si están seteadas (ej. en la
    mini-pc, montadas cada una desde su propio volumen Docker).
  - Si no, ambas caen a `./data/` local (desarrollo).
"""

import os
from pathlib import Path

import peewee
from peewee import SqliteDatabase

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

DB_PATH = os.getenv("DB_PATH", str(_DATA_DIR / "kairos.db"))
DB_HISTORICAL_PATH = os.getenv("DB_HISTORICAL_PATH", str(_DATA_DIR / "kairos_historical.db"))

# Asegura que los directorios existan (no falla si ya existen).
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
Path(DB_HISTORICAL_PATH).parent.mkdir(parents=True, exist_ok=True)

_PRAGMAS = {
    "journal_mode": "wal",       # lecturas concurrentes mientras se escribe
    "foreign_keys": 0,
    "busy_timeout": 15000,       # ms — evita "database is locked" bajo escritura concurrente
    "synchronous": 1,
    "wal_autocheckpoint": 1000,
    "cache_size": -4096,
    "temp_store": 2,
}

db = SqliteDatabase(DB_PATH, pragmas=_PRAGMAS, check_same_thread=False)
db_historical = SqliteDatabase(DB_HISTORICAL_PATH, pragmas=_PRAGMAS, check_same_thread=False)


def _add_column_if_missing(database: SqliteDatabase, table: str, col: str, sql_type: str) -> None:
    """ALTER TABLE ADD COLUMN idempotente y a prueba de carrera.

    Bug real encontrado en producción 2026-07-23: varios contenedores
    (kairos/outcome_observer_worker/limitarb_monitor_worker) comparten la
    misma DB de contabilidad y todos llaman a init_db() al arrancar. Si
    arrancan casi simultáneamente (ej. tras un `docker compose up -d
    --force-recreate` que recrea los 5 a la vez), hay una carrera TOCTOU
    real entre el `PRAGMA table_info` (lectura, "falta la columna") de
    cada proceso y el `ALTER TABLE` (escritura) del primero que gana --
    el segundo revienta con "duplicate column name" porque su chequeo de
    `existing_cols` ya estaba desactualizado cuando llegó a ejecutar el
    ALTER. WAL mode serializa escrituras pero no protege este
    read-then-write entre procesos distintos.

    Fix: intentar el ALTER directamente y tragar específicamente el error
    "duplicate column name" (columna ya existe, resultado final idéntico
    al que hubiéramos obtenido si el chequeo previo hubiera visto la
    columna) -- cualquier OTRO error se re-lanza, no se oculta un fallo
    real de un guardrail que sí importa.

    Nota: Peewee reenvuelve sqlite3.OperationalError como
    peewee.OperationalError (no es subclase de la de sqlite3) -- hay que
    capturar la de peewee, verificado con un test que reprodujo la
    carrera real antes de este fix (capturar solo sqlite3.OperationalError
    no atrapaba nada, seguía reventando)."""
    try:
        database.execute_sql(f"ALTER TABLE {table} ADD COLUMN {col} {sql_type}")
    except peewee.OperationalError as e:
        if "duplicate column name" not in str(e):
            raise


def init_db() -> None:
    """Crea las tablas que falten en la DB de contabilidad (idempotente,
    no destruye datos existentes — Peewee's create_tables usa
    CREATE TABLE IF NOT EXISTS).

    Además aplica un migration-lite para LimitArbSignal: Peewee NO hace
    ALTER TABLE automático cuando se agregan columnas a un modelo ya
    existente (create_tables con safe=True no toca tablas que ya
    existen). Las columnas de snapshot agregadas 2026-07-21
    (fav_side/fav_price_at_signal/limit_leg_side/limit_leg_price, ver
    database/accounting/limitarbsignal.py) necesitan un ALTER TABLE
    explícito en DBs que ya tenían la tabla vieja. Se revisa
    `PRAGMA table_info` y se agregan solo las columnas que falten —
    idempotente, no destruye filas existentes (quedan con NULL en las
    columnas nuevas, comportamiento esperado y documentado en el
    modelo)."""
    from database.accounting.limitarbsignal import LimitArbSignal
    from database.accounting.papertrade import PaperTrade
    from database.accounting.paperaccount import PaperAccount
    from database.accounting.fillverification import FillVerification
    from database.accounting.marketarbwebsocketsignal import MarketArbWebsocketSignal
    from database.accounting.set1entrysignal import Set1EntrySignal

    db.connect(reuse_if_open=True)
    db.create_tables([LimitArbSignal], safe=True)

    # MarketArb websocket scanner (2026-08-02, pedido explícito del
    # usuario) -- tabla nueva, no necesita ALTER TABLE.
    db.create_tables([MarketArbWebsocketSignal], safe=True)

    # Set1EntrySignal (2026-08-02, pedido explícito del usuario --
    # integración de strategies/set1_favorite_reaction.py con score en
    # vivo real) -- tabla nueva.
    db.create_tables([Set1EntrySignal], safe=True)

    # Paper-trading tables (2026-07-23, see strategies/paper_trading.py) --
    # brand-new tables, no ALTER TABLE migration needed (create_tables with
    # safe=True is CREATE TABLE IF NOT EXISTS, doesn't touch existing ones).
    db.create_tables([PaperTrade, PaperAccount], safe=True)

    # PaperAccount is a singleton (one row, id=1) -- get_or_create is
    # idempotent across repeated init_db() calls, never duplicates the row.
    PaperAccount.get_or_create(id=1, defaults={'bankroll_usd': 200.0})

    existing_cols = {row[1] for row in db.execute_sql("PRAGMA table_info(limitarbsignal)").fetchall()}
    _new_cols = {
        "fav_side": "VARCHAR(255)",
        "fav_price_at_signal": "REAL",
        "limit_leg_side": "VARCHAR(255)",
        "limit_leg_price": "REAL",
    }
    for col, sql_type in _new_cols.items():
        if col not in existing_cols:
            _add_column_if_missing(db, "limitarbsignal", col, sql_type)

    # Monitoring/exit-recommendation columns added 2026-07-22 (see
    # workers/limitarb_monitor_worker.py and limitarbsignal.py). Same
    # idempotent ALTER TABLE mechanism as above -- literal DEFAULTs only
    # for the columns that have a Python-side default in the model
    # (SQLite ALTER TABLE ADD COLUMN doesn't support non-constant
    # DEFAULT, but literal constants are fine); nullable-only columns
    # get no DEFAULT (NULL).
    _monitor_cols = {
        "market_close_time": "DATETIME",
        "monitoring_status": "VARCHAR(255) DEFAULT 'open'",
        "attempt_count": "INTEGER DEFAULT 0",
        "best_limit_gap": "REAL",
        "exit_reason": "VARCHAR(255)",
        "exit_recommended_at": "DATETIME",
        "exit_locked_profit_pct": "REAL",
    }
    for col, sql_type in _monitor_cols.items():
        if col not in existing_cols:
            _add_column_if_missing(db, "limitarbsignal", col, sql_type)

    # market_leg_side/market_leg_price_at_signal added 2026-07-23 (see
    # limitarbsignal.py) -- closes the exit_locked_profit_pct coverage
    # gap for case 'favorite_overvalued' (market leg == dog side, which
    # had no entry-price snapshot before this). Same idempotent
    # mechanism, both nullable, no DEFAULT needed.
    _market_leg_cols = {
        "market_leg_side": "VARCHAR(255)",
        "market_leg_price_at_signal": "REAL",
    }
    for col, sql_type in _market_leg_cols.items():
        if col not in existing_cols:
            _add_column_if_missing(db, "limitarbsignal", col, sql_type)

    # market_leg_ticker/limit_leg_ticker added 2026-07-23 -- real Kalshi
    # tickers per leg, fixes the cross-ticket (tenis/team-sports)
    # monitoring bug where market_id was a non-fetchable synthetic
    # event_ticker (see workers/limitarb_monitor_worker.py). Same
    # idempotent mechanism, both nullable, no DEFAULT needed.
    _real_ticker_cols = {
        "market_leg_ticker": "VARCHAR(255)",
        "limit_leg_ticker": "VARCHAR(255)",
    }
    for col, sql_type in _real_ticker_cols.items():
        if col not in existing_cols:
            _add_column_if_missing(db, "limitarbsignal", col, sql_type)

    # FillVerification (2026-07-23, see database/accounting/fillverification.py
    # and workers/fill_verification_worker.py) -- brand-new table, no
    # ALTER TABLE migration needed (create_tables with safe=True is
    # CREATE TABLE IF NOT EXISTS).
    db.create_tables([FillVerification], safe=True)

    existing_pt_cols = {row[1] for row in db.execute_sql("PRAGMA table_info(papertrade)").fetchall()}
    _papertrade_cols = {
        "close_market_leg_price": "REAL",
    }
    for col, sql_type in _papertrade_cols.items():
        if col not in existing_pt_cols:
            _add_column_if_missing(db, "papertrade", col, sql_type)


def init_db_historical() -> None:
    """Crea las tablas que falten en la DB histórica/de referencia
    (idempotente). Las tablas rescatadas (KalshiMarketTick/
    CryptoPriceSnapshot/TeamRating) YA existen con datos reales en el
    volumen viejo — create_tables con safe=True no las toca ni las
    recrea. Las tablas de tenis/team_sports nuevas se crean vacías la
    primera vez que corre esto."""
    from database.historical.market_data import KalshiMarketTick, CryptoPriceSnapshot
    from database.historical.team_sports import (
        TeamRating, TeamMatchHistory, TeamHeadToHead, MlbScannedMatch, MlbMatchProbabilityTick,
    )
    from database.historical.tennis import (
        TennisPlayerRanking, TennisServeCalibration, TennisHistoricalOdds, TennisHeadToHead,
        TennisAbstractElo, TennisAbstractYelo,
        TennisAbstractCareerSplit, TennisAbstractH2H,
        TennisScannedMatch, TennisMatchProbabilityTick, TennisRecentResult,
        TennisMatchWeather, TennisMatchStats, TennisMatchStatsSet,
        TournamentLocationCache,
        WtaPlayerRankingHistory, WtaTournamentLevel, AtpTournamentLevel, TennisExplorerPlayerSlug,
        MatchHistory, Player, TennisMatchHistoryUnvalidated, TennisPlayerSyncStatus,
        TennisPlayerWikidataReference,
    )
    # `TennisMatchStatsSet` (2026-08-09, pedido explícito del usuario --
    # match.set_stats): estaba definida en models.py pero NUNCA se había
    # registrado acá -- gap real preexistente, la tabla no existía en
    # producción. Se crea limpia con el esquema nuevo (match/match_history
    # ambos nullable desde el día 1, sin el problema de NOT NULL legacy
    # que sí tuvo MatchWeather).
    # ChartedMatch/ChartedMatchStats -- REMOVIDOS 2026-08-09 (ver
    # models.py), ya fusionados en MatchHistory/MatchStats.
    from database.historical.baseball import MlbGameHistory, MlbHistoricalOdds

    db_historical.connect(reuse_if_open=True)
    db_historical.create_tables([
        KalshiMarketTick, CryptoPriceSnapshot, TeamRating,
        TennisPlayerRanking, TennisServeCalibration, TennisHistoricalOdds, TennisHeadToHead,
        TennisAbstractElo, TennisAbstractYelo,
        TennisAbstractCareerSplit, TennisAbstractH2H,
        TennisScannedMatch, TennisMatchProbabilityTick, TennisRecentResult,
        TennisMatchWeather, TennisMatchStats, TennisMatchStatsSet,
        TournamentLocationCache,
        # 2026-08-10, pedido explícito del usuario -- entidad canónica de
        # jugador/a ("los datos de player deben ir en el modelo de
        # player"), referenciada por MatchHistory.player_a/player_b (FK,
        # ver abajo) -- Fase 1 de la migración, tabla nueva.
        Player,
        # 2026-08-08, pedido explícito del usuario -- 1 fila por partido
        # real (no 2 como TennisMatchHistory), con identidad resuelta
        # con certeza vía match-detail de TennisExplorer. Tabla nueva.
        MatchHistory,
        # 2026-08-08, pedido explícito del usuario -- candidatos de dedup
        # que no se pudieron validar contra Sackmann, "en una tabla
        # aparte" (nunca se descartan). Tabla nueva.
        TennisMatchHistoryUnvalidated,
        # 2026-08-09, pedido explícito del usuario -- cola de prioridad +
        # frescura de sync por jugador (engine.py nunca fetchea en vivo,
        # solo lee esto). Tabla nueva.
        TennisPlayerSyncStatus,
        TeamMatchHistory, TeamHeadToHead, MlbGameHistory,
        # 2026-08-05, pedido explícito del usuario -- tracking de scan +
        # ticks tick-by-tick para MLB, mismo patrón que tenis (tablas
        # nuevas, no requieren ALTER TABLE).
        MlbScannedMatch, MlbMatchProbabilityTick,
        # 2026-08-05, pedido explícito del usuario -- odds pre-partido
        # reales (SportsbookReviewsOnline), tabla nueva.
        MlbHistoricalOdds,
        # 2026-08-05, pedido explícito del usuario -- ranking semanal
        # real WTA, tabla nueva.
        WtaPlayerRankingHistory,
        # 2026-08-05, pedido explícito del usuario -- categoría real de
        # torneo WTA (250/500/1000/Slam vs ITF/125), tabla nueva.
        WtaTournamentLevel, AtpTournamentLevel,
        # 2026-08-06, pedido explícito del usuario -- cache de slugs
        # reales de TennisExplorer cosechados de hrefs, tabla nueva.
        TennisExplorerPlayerSlug,
        # 2026-08-10, pedido explícito del usuario -- base de verdad
        # nombre/apellido de jugadores de tenis vía Wikidata, tabla
        # nueva (ver TennisPlayerWikidataReference).
        TennisPlayerWikidataReference,
    ], safe=True)

    # ALTER TABLE idempotente (2026-08-01, pedido explícito del usuario):
    # `tournament` se agregó a TennisScannedMatch DESPUÉS de que la tabla
    # ya existiera en producción -- create_tables(safe=True) no la agrega
    # sola, ver _add_column_if_missing arriba.
    _add_column_if_missing(db_historical, "tennis_scanned_match", "tournament", "VARCHAR(255)")
    _add_column_if_missing(db_historical, "tennis_recent_result", "round", "VARCHAR(255)")
    _add_column_if_missing(db_historical, "tennis_scanned_match", "round", "VARCHAR(255)")
    # 2026-08-08, pedido explícito del usuario -- worker nuevo que
    # sincroniza MatchHistory/ranking/stats por partido detectado.
    _add_column_if_missing(db_historical, "tennis_scanned_match", "history_synced_at", "DATETIME")
    # 2026-08-08, revisión del consolidado de modelos (pedido explícito
    # del usuario) -- `MatchStats` (database/historical/tennis/models.py)
    # agrega la FK `match` (columna real `match_id`) + 28 columnas de
    # stats tipificadas nuevas sobre la tabla YA EXISTENTE
    # `tennis_match_stats` (66 filas reales en producción al momento de
    # este cambio) -- `create_tables(safe=True)` NUNCA hace ALTER TABLE
    # sobre una tabla ya creada, así que sin esto cualquier lectura real
    # (`MatchStats.select()`) hubiera fallado con "no such column".
    # Todas nullable -- filas existentes quedan con NULL, fail-closed,
    # nunca se inventa un valor.
    _add_column_if_missing(db_historical, "tennis_match_stats", "match_id", "INTEGER")
    for _col, _sql_type in [
        ("aces_a", "INTEGER"), ("aces_b", "INTEGER"),
        ("double_faults_a", "INTEGER"), ("double_faults_b", "INTEGER"),
        ("winners_a", "INTEGER"), ("winners_b", "INTEGER"),
        ("unforced_errors_a", "INTEGER"), ("unforced_errors_b", "INTEGER"),
        ("total_points_won_a", "INTEGER"), ("total_points_won_b", "INTEGER"),
        ("service_games_won_a", "INTEGER"), ("service_games_won_b", "INTEGER"),
        ("return_games_won_a", "INTEGER"), ("return_games_won_b", "INTEGER"),
        ("first_serve_pct_a", "REAL"), ("first_serve_pct_b", "REAL"),
        ("second_serve_pct_a", "REAL"), ("second_serve_pct_b", "REAL"),
        ("first_serve_points_won_pct_a", "REAL"), ("first_serve_points_won_pct_b", "REAL"),
        ("second_serve_points_won_pct_a", "REAL"), ("second_serve_points_won_pct_b", "REAL"),
        ("break_points_won_a", "INTEGER"), ("break_points_won_b", "INTEGER"),
        ("break_points_faced_a", "INTEGER"), ("break_points_faced_b", "INTEGER"),
        ("break_points_saved_a", "INTEGER"), ("break_points_saved_b", "INTEGER"),
        ("serve_games_played_a", "INTEGER"), ("serve_games_played_b", "INTEGER"),
    ]:
        _add_column_if_missing(db_historical, "tennis_match_stats", _col, _sql_type)
    # 2026-08-08, pedido explícito del usuario -- fusión de
    # `ChartedMatchStats` dentro de `MatchStats`: nueva FK opcional a
    # `MatchHistory` (columna real `match_history_id`, NO reemplaza la
    # FK legacy `match_id` -- ver docstring del modelo) + `source` para
    # distinguir origen ("flashscore" default retrocompatible con las
    # 66 filas reales, o "sackmann_match_charting_project").
    _add_column_if_missing(db_historical, "tennis_match_stats", "match_history_id", "INTEGER")
    _add_column_if_missing(db_historical, "tennis_match_stats", "source", "VARCHAR(255) DEFAULT 'flashscore'")
    # 2026-08-09, pedido explícito del usuario -- "en MatchHistory está
    # ATP y WTA, mezclados... añadir tour" + "que esté la referencia a
    # clima". `tour` nullable, fail-closed (nunca se adivina). `match_history`
    # en `tennis_match_weather` mismo patrón que arriba -- OJO: `match_id`
    # en esa tabla es NOT NULL real en producción (233 filas, verificado
    # con PRAGMA table_info) y SQLite no permite relajarlo vía ALTER
    # TABLE -- escribir clima para un partido que SOLO existe en
    # `MatchHistory` (sin tracking vivo en `Match`) va a fallar hasta
    # que se haga un rebuild real de esa tabla. Se deja documentado,
    # NO se fuerza un `Match` falso para evitarlo (sería peor: un
    # vínculo mentiroso a un partido de Kalshi que no tiene nada que
    # ver).
    _add_column_if_missing(db_historical, "match_history", "tour", "VARCHAR(255)")
    _add_column_if_missing(db_historical, "match_history", "winner_age", "REAL")
    _add_column_if_missing(db_historical, "match_history", "loser_age", "REAL")
    _add_column_if_missing(db_historical, "match_history", "minutes", "INTEGER")
    _add_column_if_missing(db_historical, "match_history", "best_of", "INTEGER")
    _add_column_if_missing(db_historical, "match_history", "level", "VARCHAR(255)")
    # 2026-08-10, pedido explícito del usuario -- Fase 1 de la migración
    # a entidad canónica `Player` ("los datos de player deben ir en el
    # modelo de player, y simplemente referenciar a los players en los
    # matches"): FK nuevas en `match_history`, ya existente en
    # producción -- ALTER TABLE idempotente, mismo patrón que el resto.
    # `INTEGER` (no una FK real a nivel SQL -- SQLite no aplica FK
    # constraints vía ALTER TABLE de todas formas, Peewee la modela como
    # FK a nivel Python/ORM igual).
    _add_column_if_missing(db_historical, "match_history", "player_a_id", "INTEGER")
    _add_column_if_missing(db_historical, "match_history", "player_b_id", "INTEGER")
    # `tennis_match_stats_set.match_history_id` -- defensivo, la tabla
    # se crea limpia con esta columna vía create_tables() en la mayoría
    # de los entornos, pero por si ya existía sin ella en algún dev DB.
    _add_column_if_missing(db_historical, "tennis_match_stats_set", "match_history_id", "INTEGER")
    _add_column_if_missing(db_historical, "tennis_match_weather", "match_history_id", "INTEGER")
    # `charted_match` -- migraciones de columna REMOVIDAS 2026-08-09
    # junto con el modelo (ver models.py): la tabla/clase ya no existe,
    # `create_tables()` ya no la crea, este ALTER TABLE fallaría siempre.
    # `tennis_match_history` -- migraciones de columna REMOVIDAS
    # 2026-08-09 junto con el modelo `TennisMatchHistory` (ver
    # models.py): su data se migró a `MatchHistory`, la tabla/clase ya
    # no existe, `create_tables()` ya no la crea, este ALTER TABLE
    # fallaría siempre.
    # 2026-08-05, pedido explícito del usuario: clima real para MLB
    # (game_datetime_utc real de la fuente, no aproximado como tenis) --
    # agregado DESPUÉS de que mlb_game_history ya existiera con 26,882
    # filas reales (backfill 2015-2026 corrido antes de este cambio).
    _add_column_if_missing(db_historical, "mlb_game_history", "game_datetime_utc", "VARCHAR(255)")
    _add_column_if_missing(db_historical, "mlb_game_history", "temperature_c", "REAL")
    _add_column_if_missing(db_historical, "mlb_game_history", "humidity_pct", "REAL")
    _add_column_if_missing(db_historical, "mlb_game_history", "wind_kmh", "REAL")
    _add_column_if_missing(db_historical, "mlb_game_history", "weather_fetched_at", "DATETIME")
