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
    database/models_historical.py).

Rescatado (patrón, no el código completo) de la arquitectura pre-"extreme
makeover" (`src/database/database.py`, borrado en `02d7f35`): mismo motor
(SQLite + WAL) para ambas bases, mismo mecanismo de ruta configurable vía
env var. El resto del schema viejo (Player/Wallet/BetMarket/Event/... —
decenas de modelos Peewee de la arquitectura multi-plataforma con paper
trading que ya no existe) NO se rescata salvo las tablas con datos reales
(ver database/models_historical.py) — el resto estaban vacías, confirmado
por inspección directa.

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
    CREATE TABLE IF NOT EXISTS)."""
    from database.models import LimitArbSignal

    db.connect(reuse_if_open=True)
    db.create_tables([LimitArbSignal], safe=True)


def init_db_historical() -> None:
    """Crea las tablas que falten en la DB histórica/de referencia
    (idempotente). Las tablas rescatadas (KalshiMarketTick/
    CryptoPriceSnapshot/TeamRating) YA existen con datos reales en el
    volumen viejo — create_tables con safe=True no las toca ni las
    recrea. Las tablas de tenis (models_historical_tennis.py) son NUEVAS,
    se crean vacías la primera vez que corre esto."""
    from database.models_historical import KalshiMarketTick, CryptoPriceSnapshot, TeamRating
    from database.models_historical_tennis import (
        TennisPlayerRanking, TennisServeCalibration, TennisMatchHistory, TennisHeadToHead,
    )

    db_historical.connect(reuse_if_open=True)
    db_historical.create_tables([
        KalshiMarketTick, CryptoPriceSnapshot, TeamRating,
        TennisPlayerRanking, TennisServeCalibration, TennisMatchHistory, TennisHeadToHead,
    ], safe=True)
