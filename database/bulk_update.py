"""UPDATE masivo por clave primaria para recomputes que reescriben columnas
derivadas de muchas filas (p. ej. el Elo point-in-time de `MatchHistory`).

`Model.save()` fila a fila emite un UPDATE por fila (un round trip a
PostgreSQL cada uno, y con todas las columnas si no se pasa `only=`); con
cientos de miles de filas eso domina el tiempo y la CPU del worker. Acá se
agrupan en lotes:

  - PostgreSQL: `UPDATE ... FROM (VALUES ...)` vía `execute_values`, un
    statement por lote. Cada valor lleva un cast explícito al tipo de la
    columna para que PostgreSQL no infiera `numeric`/`text` en el VALUES.
  - SQLite (sólo tests): `executemany` sobre un UPDATE parametrizado.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from peewee import Database, Field, Model, PostgresqlDatabase

DEFAULT_BATCH_SIZE = 5000


def _chunks(rows: Iterable[tuple], size: int):
    batch: list[tuple] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


# PK autoincrementales -> su entero. Floats -> DOUBLE PRECISION para no
# truncar a float4 si la columna real es más ancha que lo que declara
# Peewee (`FloatField` = REAL); la conversión a la columna la hace el
# cast de asignación de PostgreSQL.
_CAST_OVERRIDES = {"SERIAL": "INTEGER", "BIGSERIAL": "BIGINT", "REAL": "DOUBLE PRECISION"}


def _sql_type(database: Database, field: Field) -> str:
    """Tipo para el cast del VALUES, a partir del DDL que genera Peewee."""
    ctx = database.get_sql_context()
    sql_type, _ = ctx.sql(field.ddl_datatype(ctx)).query()
    return _CAST_OVERRIDES.get(sql_type.upper(), sql_type)


def bulk_update_by_id(
    database: Database,
    model: type[Model],
    fields: Sequence[Field],
    rows: Iterable[tuple],
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    """Actualiza `fields` de `model` para cada `(pk, valor_1, ..., valor_n)`
    de `rows` (mismo orden que `fields`). Debe llamarse dentro de una
    conexión abierta; la transacción la decide el caller. Devuelve la
    cantidad de tuplas enviadas."""
    table = model._meta.table_name
    pk = model._meta.primary_key
    columns = [field.column_name for field in fields]
    total = 0

    if isinstance(database, PostgresqlDatabase):
        from psycopg2.extras import execute_values

        assignments = ", ".join(f'"{c}" = v."{c}"' for c in columns)
        aliases = ", ".join(f'"{c}"' for c in [pk.column_name, *columns])
        template = "(" + ", ".join(
            f"%s::{_sql_type(database, field)}" for field in (pk, *fields)
        ) + ")"
        sql = (
            f'UPDATE "{table}" AS t SET {assignments} '
            f'FROM (VALUES %s) AS v({aliases}) '
            f'WHERE t."{pk.column_name}" = v."{pk.column_name}"'
        )
        cursor = database.cursor()
        for batch in _chunks(rows, batch_size):
            execute_values(cursor, sql, batch, template=template, page_size=len(batch))
            total += len(batch)
        return total

    assignments = ", ".join(f'"{c}" = ?' for c in columns)
    sql = f'UPDATE "{table}" SET {assignments} WHERE "{pk.column_name}" = ?'
    cursor = database.cursor()
    for batch in _chunks(rows, batch_size):
        cursor.executemany(sql, [(*values, pk_value) for pk_value, *values in batch])
        total += len(batch)
    return total
