"""Runner mínimo de migraciones PostgreSQL, sin dependencias externas.

Cada base lógica mantiene su propio historial. Las migraciones se ejecutan
con un advisory lock para que el arranque simultáneo de varios workers no
pueda aplicar el mismo cambio dos veces.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Callable, Sequence

from peewee import Database, Model


MIGRATION_TABLE = "kairos_schema_migration"
_LOCK_NAMESPACE = 1_267_469_767  # "KAIROS" como entero estable de 32 bits.


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[Database], None]

    @property
    def checksum(self) -> str:
        identity = f"{self.version}:{self.name}:{self.apply.__module__}:{self.apply.__name__}"
        return sha256(identity.encode()).hexdigest()


def _ensure_history_table(database: Database) -> None:
    database.execute_sql(
        f"""CREATE TABLE IF NOT EXISTS {MIGRATION_TABLE} (
            version INTEGER PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            checksum VARCHAR(64) NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )


def _applied(database: Database) -> dict[int, tuple[str, str]]:
    rows = database.execute_sql(
        f"SELECT version, name, checksum FROM {MIGRATION_TABLE} ORDER BY version"
    ).fetchall()
    return {int(version): (name, checksum) for version, name, checksum in rows}


def run_migrations(database: Database, migrations: Sequence[Migration], *, database_key: int) -> None:
    """Aplica migraciones pendientes una vez y en orden estricto."""
    database.connect(reuse_if_open=True)
    database.execute_sql("SELECT pg_advisory_lock(%s, %s)", (_LOCK_NAMESPACE, database_key))
    try:
        _ensure_history_table(database)
        applied = _applied(database)
        versions = [migration.version for migration in migrations]
        if versions != sorted(set(versions)):
            raise RuntimeError("Las migraciones deben tener versiones únicas y orden ascendente")

        known = set(versions)
        unknown = sorted(set(applied) - known)
        if unknown:
            raise RuntimeError(f"La base contiene migraciones desconocidas para este código: {unknown}")

        for migration in migrations:
            previous = applied.get(migration.version)
            if previous:
                if previous != (migration.name, migration.checksum):
                    raise RuntimeError(
                        f"La migración {migration.version} fue modificada después de aplicarse"
                    )
                continue
            with database.atomic():
                migration.apply(database)
                database.execute_sql(
                    f"INSERT INTO {MIGRATION_TABLE} (version, name, checksum) VALUES (%s, %s, %s)",
                    (migration.version, migration.name, migration.checksum),
                )
    finally:
        database.execute_sql("SELECT pg_advisory_unlock(%s, %s)", (_LOCK_NAMESPACE, database_key))


def validate_model_schema(database: Database, models: Sequence[type[Model]]) -> None:
    """Comprueba tablas y columnas después de migrar; no altera por inferencia."""
    failures: list[str] = []
    tables = set(database.get_tables())
    for model in models:
        table = model._meta.table_name
        if table not in tables:
            failures.append(f"falta tabla {table}")
            continue
        actual = {column.name for column in database.get_columns(table)}
        expected = {field.column_name for field in model._meta.sorted_fields}
        missing = sorted(expected - actual)
        if missing:
            failures.append(f"{table}: faltan columnas {', '.join(missing)}")
    if failures:
        raise RuntimeError(
            "Esquema PostgreSQL incompatible después de migrar: " + "; ".join(failures)
        )

