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
`models/volatility/sports/` y `models/fairprice/sports/` — un archivo por
modelo, un `writer.py` por tema cuando aplica):
    database/
    ├── db.py                       (este archivo — conexiones + init)
    ├── accounting/                 (db, DB_PATH — operativo)
    │   ├── limitarbsignal.py
    │   └── writer.py
    └── historical/                 (db_historical, DB_HISTORICAL_PATH — referencia)
        ├── market_data/            (KalshiMarketTick, CryptoPriceSnapshot — rescatadas)
        ├── team_sports/            (TeamRating, TeamMatchHistory, TeamHeadToHead + writer.py)
        └── tennis/                 (Tennis* x4 + writer.py)

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

    db.connect(reuse_if_open=True)
    db.create_tables([LimitArbSignal], safe=True)

    existing_cols = {row[1] for row in db.execute_sql("PRAGMA table_info(limitarbsignal)").fetchall()}
    _new_cols = {
        "fav_side": "VARCHAR(255)",
        "fav_price_at_signal": "REAL",
        "limit_leg_side": "VARCHAR(255)",
        "limit_leg_price": "REAL",
    }
    for col, sql_type in _new_cols.items():
        if col not in existing_cols:
            db.execute_sql(f"ALTER TABLE limitarbsignal ADD COLUMN {col} {sql_type}")

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
            db.execute_sql(f"ALTER TABLE limitarbsignal ADD COLUMN {col} {sql_type}")

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
            db.execute_sql(f"ALTER TABLE limitarbsignal ADD COLUMN {col} {sql_type}")


def init_db_historical() -> None:
    """Crea las tablas que falten en la DB histórica/de referencia
    (idempotente). Las tablas rescatadas (KalshiMarketTick/
    CryptoPriceSnapshot/TeamRating) YA existen con datos reales en el
    volumen viejo — create_tables con safe=True no las toca ni las
    recrea. Las tablas de tenis/team_sports nuevas se crean vacías la
    primera vez que corre esto."""
    from database.historical.market_data import KalshiMarketTick, CryptoPriceSnapshot
    from database.historical.team_sports import TeamRating, TeamMatchHistory, TeamHeadToHead
    from database.historical.tennis import (
        TennisPlayerRanking, TennisServeCalibration, TennisMatchHistory, TennisHeadToHead,
    )

    db_historical.connect(reuse_if_open=True)
    db_historical.create_tables([
        KalshiMarketTick, CryptoPriceSnapshot, TeamRating,
        TennisPlayerRanking, TennisServeCalibration, TennisMatchHistory, TennisHeadToHead,
        TeamMatchHistory, TeamHeadToHead,
    ], safe=True)
