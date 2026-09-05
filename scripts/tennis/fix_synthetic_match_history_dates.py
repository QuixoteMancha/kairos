"""Recalcula `MatchHistory.match_date` de las filas sintéticas de Kalshi
(`match_id` = `"kalshi:{market_id}"`) contra `resolve_scanned_match_date`
-- 2026-09-05, pedido explícito del usuario ("podemos arreglar el bug de
close_time en las filas sintéticas?").

BUG REAL: `resolve_scanned_match_date` (factorizado 2026-08-28) prioriza
la fecha embebida en el ticker de Kalshi sobre `close_time` (que puede
ser la expiración de la SERIE, semanas después del partido real -- ver
su propio docstring). Pero esta función solo se llama al CREAR la fila
sintética (`ensure_match_history_for_scanned_match`) -- filas creadas
ANTES de ese fix, o cuyo `close_time` se resolvió mal en su momento por
otra razón, se quedaron con la fecha vieja para siempre; nadie las
vuelve a recalcular. Medido en producción: 310 de 510 filas sintéticas
(60%) tienen HOY un `match_date` distinto al que `resolve_scanned_match_date`
calcularía con el mismo `market_id`/`close_time` ya guardado.

IMPORTANTE -- límite conocido (encontrado por Codex 2026-09-05
investigando los últimos 11 casos de jugadores duplicados): el ticker
de Kalshi es la fecha de LISTADO del mercado, no necesariamente la
fecha exacta del partido -- el partido real puede ser hasta 3 días
DESPUÉS. Este script sigue siendo una mejora real (corrige rechazos de
semanas de diferencia por `close_time` mal usado) pero NO garantiza la
fecha exacta al día -- para eso hace falta el partido real confirmado
vía `get_match_detail`/`tennisexplorer_identity.py`, no este fix.

Solo toca filas con `match_id` que empieza con `"kalshi:"` (nunca las
reales de `source="tennisexplorer"`). Fail-closed: una fila sin
`Match` (`tennis_scanned_match`) vinculado, o cuya fecha no se puede
resolver, se deja intacta.

Por default corre en modo DRY-RUN. Pasar `--apply` para escribir los
cambios de verdad."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from database.db import db_historical, init_db_historical
from database.historical.tennis.matchtracking import resolve_scanned_match_date
from database.historical.tennis.models import Match, MatchHistory


def main() -> None:
    apply = "--apply" in sys.argv
    mode = "APLICANDO CAMBIOS" if apply else "DRY-RUN (nada se modifica)"
    print(f"Modo: {mode}\n")

    init_db_historical()
    with db_historical.connection_context():
        rows = MatchHistory.select().where(MatchHistory.match_id.startswith("kalshi:"))

        n_total = 0
        n_changed = 0
        n_no_scanned_match = 0
        n_unresolvable = 0

        for row in rows:
            n_total += 1
            scanned = Match.get_or_none(Match.match_history == row.id)
            if scanned is None:
                n_no_scanned_match += 1
                continue
            resolved = resolve_scanned_match_date(scanned.market_id, scanned.close_time)
            if resolved is None:
                n_unresolvable += 1
                continue
            if resolved == row.match_date:
                continue
            n_changed += 1
            print(f"id={row.id} market_id={scanned.market_id!r} "
                  f"match_date: {row.match_date!r} -> {resolved!r}")
            if apply:
                row.match_date = resolved
                row.save(only=[MatchHistory.match_date])

    print(f"\nResumen: total={n_total}  corregidos={n_changed}  "
          f"sin_scanned_match={n_no_scanned_match}  sin_fecha_resoluble={n_unresolvable}")
    if not apply:
        print("(dry-run: correr de nuevo con --apply para escribir los cambios de verdad)")


if __name__ == "__main__":
    main()
