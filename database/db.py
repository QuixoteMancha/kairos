"""
Capa de persistencia de Kairos — SQLite vía Peewee.

Rescatado (patrón, no el código completo) de la arquitectura pre-"extreme
makeover" (`src/database/database.py`, borrado en `02d7f35`): mismo motor
(SQLite + WAL), mismo mecanismo de ruta configurable vía `DB_PATH`. El
resto del schema viejo (Player/Wallet/BetMarket/Event/... — decenas de
modelos Peewee) NO se rescata: pertenecía a una arquitectura multi-
plataforma con paper trading, wallets, settlement, etc. que ya no existe
en este repo. Este archivo arranca mínimo, con solo lo que el proyecto
reconstruido necesita hoy (ver database/models.py), y crece a medida que
se agreguen features reales.

Ruta de la DB:
  - `DB_PATH` en `.env` si está seteado (ej. en la mini-pc de producción,
    `/data/kairos.db`, fuera de la capa de imagen Docker — ver
    kairos-env-file-location-and-vars en memoria).
  - Si no, `./data/kairos.db` (relativo al repo, para desarrollo local).
"""

import os
from pathlib import Path

from peewee import SqliteDatabase

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

_DEFAULT_DB_PATH = str(Path(__file__).resolve().parent.parent / "data" / "kairos.db")
DB_PATH = os.getenv("DB_PATH", _DEFAULT_DB_PATH)

# Asegura que el directorio de la DB exista (no falla si ya existe).
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

db = SqliteDatabase(
    DB_PATH,
    pragmas={
        "journal_mode": "wal",       # lecturas concurrentes mientras se escribe
        "foreign_keys": 0,
        "busy_timeout": 15000,       # ms — evita "database is locked" bajo escritura concurrente
        "synchronous": 1,
        "wal_autocheckpoint": 1000,
        "cache_size": -4096,
        "temp_store": 2,
    },
    check_same_thread=False,
)


def init_db() -> None:
    """Crea las tablas que falten (idempotente, no destruye datos
    existentes — Peewee's create_tables usa CREATE TABLE IF NOT EXISTS)."""
    from database.models import LimitArbSignal

    db.connect(reuse_if_open=True)
    db.create_tables([LimitArbSignal], safe=True)
