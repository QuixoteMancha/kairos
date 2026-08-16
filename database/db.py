"""Capa de persistencia de Kairos — PostgreSQL vía Peewee.

Producción usa dos bases lógicas separadas en el mismo servidor:

  - `db` (`DB_NAME`): operativa del sistema — señales de LimitArb hoy,
    órdenes/fills/PnL más adelante.
  - `db_historical` (`DB_HISTORICAL_NAME`): datos
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

Las bases activas son exclusivamente PostgreSQL. SQLite sólo se permite
como adaptador efímero del suite unitario mediante ``KAIROS_TESTING=1``;
no existe como opción de despliegue.
"""

import os
from pathlib import Path

from peewee import Database, PostgresqlDatabase, SqliteDatabase

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_BACKEND = os.getenv("KAIROS_DB_BACKEND", "postgresql").lower()

# SQLite queda disponible únicamente como backend liviano para el suite de
# tests. Producción/Docker usa PostgreSQL por defecto.
DB_PATH = os.getenv("DB_PATH", str(_DATA_DIR / "kairos.db"))
DB_HISTORICAL_PATH = os.getenv("DB_HISTORICAL_PATH", str(_DATA_DIR / "kairos_historical.db"))

_PRAGMAS = {
    "journal_mode": "wal",       # lecturas concurrentes mientras se escribe
    "foreign_keys": 0,
    "busy_timeout": 15000,       # ms — evita "database is locked" bajo escritura concurrente
    "synchronous": 1,
    "wal_autocheckpoint": 1000,
    "cache_size": -4096,
    "temp_store": 2,
}

if DB_BACKEND == "sqlite" and os.getenv("KAIROS_TESTING") == "1":
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    Path(DB_HISTORICAL_PATH).parent.mkdir(parents=True, exist_ok=True)
    db: Database = SqliteDatabase(DB_PATH, pragmas=_PRAGMAS, check_same_thread=False)
    db_historical: Database = SqliteDatabase(
        DB_HISTORICAL_PATH, pragmas=_PRAGMAS, check_same_thread=False,
    )
elif DB_BACKEND in {"postgres", "postgresql"}:
    connection = {
        "user": os.getenv("DB_USER", "kairos"),
        "password": os.getenv("DB_PASSWORD", "kairos"),
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", "5432")),
    }
    db = PostgresqlDatabase(os.getenv("DB_NAME", "kairos"), **connection)
    db_historical = PostgresqlDatabase(
        os.getenv("DB_HISTORICAL_NAME", "kairos_historical"), **connection,
    )
else:
    raise ValueError(
        f"KAIROS_DB_BACKEND no soportado: {DB_BACKEND!r}; Kairos requiere PostgreSQL"
    )


def init_db() -> None:
    """Migra y valida la base PostgreSQL de contabilidad."""
    from database.accounting.paperaccount import PaperAccount
    from database.accounting.systemhaltstate import SystemHaltState
    from database.migrations.catalog import ACCOUNTING_MIGRATIONS, accounting_models
    from database.migrations.runner import run_migrations, validate_model_schema

    db.connect(reuse_if_open=True)
    if DB_BACKEND == "sqlite":  # Adaptador unitario; producción nunca entra aquí.
        db.create_tables(accounting_models(), safe=True)
    else:
        run_migrations(db, ACCOUNTING_MIGRATIONS, database_key=1)
        validate_model_schema(db, accounting_models())

    # PaperAccount is a singleton (one row, id=1) -- get_or_create is
    # idempotent across repeated init_db() calls, never duplicates the row.
    PaperAccount.get_or_create(id=1, defaults={'bankroll_usd': 200.0})

    # SystemHaltState (2026-08-11, pedido explícito del usuario -- "si
    # efectivamente se comprueba que no hay fondos, se deben interrumpir
    # todas las operaciones") -- circuit-breaker global, singleton
    # (id=1), mismo criterio que PaperAccount arriba.
    SystemHaltState.get_or_create(id=1, defaults={"halted": False})


def init_db_historical() -> None:
    """Crea las tablas que falten en la DB histórica/de referencia
    (idempotente). Las tablas rescatadas (KalshiMarketTick/
    CryptoPriceSnapshot/TeamRating) YA existen con datos reales en el
    volumen viejo — create_tables con safe=True no las toca ni las
    recrea. Las tablas de tenis/team_sports nuevas se crean vacías la
    primera vez que corre esto."""
    from database.migrations.catalog import HISTORICAL_MIGRATIONS, historical_models
    from database.migrations.runner import run_migrations, validate_model_schema

    db_historical.connect(reuse_if_open=True)
    if DB_BACKEND == "sqlite":
        db_historical.create_tables(historical_models(), safe=True)
    else:
        run_migrations(db_historical, HISTORICAL_MIGRATIONS, database_key=2)
        validate_model_schema(db_historical, historical_models())
