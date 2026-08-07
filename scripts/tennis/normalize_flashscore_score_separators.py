"""Migración: normaliza el separador de sets en `TennisMatchHistory.score`
para las filas `source="flashscore"` que vinieron con espacio en vez de
coma -- 2026-08-06, pedido explícito del usuario: "aplica un update a
los scores que separe los marcadores de set".

Contexto real (ver `wta_favorite_surface_winrate_filter_backtest.py::
_normalize_score_separators`, ya arreglaba esto AL LEER, sin tocar la
base): `TennisMatchHistory` es una tabla compartida entre 2 scrapers
con convenciones de separador distintas -- TennisExplorer (coma, ej.
"6-4, 4-6, 6-3") y Flashscore (espacio, ej. "7-6 6-4", pipeline más
viejo/paralelo que sigue activo). El fix de lectura ya cubría el
riesgo funcional; esta migración además deja el DATO CRUDO consistente
en la base, para que cualquier consumidor futuro que lea `.score`
directo (sin pasar por esas funciones) no vuelva a pisar el mismo
problema.

Reusa exactamente la misma normalización ya probada (comportamiento
IDÉNTICO al de lectura, no una reimplementación paralela) -- solo
reescribe si el resultado normalizado es REALMENTE distinto del
original (nunca toca una fila que ya viene con coma, ni una sin sets
múltiples reales que normalizar). Restringido a `source="flashscore"`
(alcance exacto del problema real medido: 7,738/8,050 filas), no toca
`source="tennisexplorer"` (aunque sería un no-op ahí de todos modos,
por disciplina de alcance)."""

from __future__ import annotations

from database.db import init_db_historical, db_historical
from database.historical.tennis import TennisMatchHistory
from scripts.tennis.wta_favorite_surface_winrate_filter_backtest import _normalize_score_separators


def run_migration(dry_run: bool = False) -> dict:
    init_db_historical()
    updated = 0
    unchanged = 0
    empty = 0

    with db_historical.connection_context():
        rows = list(TennisMatchHistory.select().where(TennisMatchHistory.source == "flashscore"))
        for row in rows:
            if not row.score:
                empty += 1
                continue
            normalized = _normalize_score_separators(row.score)
            if normalized == row.score:
                unchanged += 1
                continue
            if not dry_run:
                row.score = normalized
                row.save()
            updated += 1

    summary = {
        "total_flashscore_rows": len(rows), "updated": updated,
        "unchanged": unchanged, "empty_score": empty, "dry_run": dry_run,
    }
    print(f"[normalize_flashscore_score_separators] TERMINADO: {summary}", flush=True)
    return summary


if __name__ == "__main__":
    import sys
    run_migration(dry_run=("--dry-run" in sys.argv))
