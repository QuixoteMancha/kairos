"""Selección rotativa para enrichments que pueden fallar por fila.

Un ``LIMIT`` fijo sobre los primeros pendientes hace que unas pocas filas
imposibles de resolver bloqueen para siempre todo lo que viene después. Este
helper conserva un cursor en el proceso worker: avanza por PK y, al llegar al
final, vuelve al comienzo para reintentar los fallos en una pasada posterior.
"""

from __future__ import annotations

from typing import Any


def take_rotating_batch(
    query, id_field, *, after_id: int, limit: int, cursor_key: str | None = None,
) -> tuple[list[Any], int]:
    """Devuelve hasta ``limit`` filas posteriores al cursor, con wrap-around.

    ``query`` debe contener exclusivamente filas todavía pendientes. El cursor
    es deliberadamente de proceso: los workers son persistentes y no hace falta
    agregar estado operativo al esquema histórico. Tras un reinicio se comienza
    desde el principio, pero dentro de la misma corrida ninguna fila fallida
    puede bloquear a las siguientes.
    """
    if cursor_key:
        from database.historical.workerstate import get_worker_cursor
        after_id = get_worker_cursor(cursor_key, after_id)
    if limit <= 0:
        return [], after_id

    rows = list(
        query.where(id_field > after_id).order_by(id_field.asc()).limit(limit)
    )
    if len(rows) < limit:
        rows.extend(
            query.where(id_field <= after_id)
            .order_by(id_field.asc())
            .limit(limit - len(rows))
        )

    next_after_id = rows[-1].id if rows else 0
    if cursor_key:
        from database.historical.workerstate import set_worker_cursor
        set_worker_cursor(cursor_key, next_after_id)
    return rows, next_after_id
