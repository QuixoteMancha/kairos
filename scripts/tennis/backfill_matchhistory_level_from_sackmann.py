"""Backfill de `MatchHistory.level` para filas `source="tennisexplorer"`,
derivado directo del catálogo de niveles YA presente en las filas
`source="sackmann"` -- 2026-08-09, pedido explícito del usuario:
"tennisexplorer expone el torneo, se puede hacer un map para derivar/
deducir el level del torneo".

Sackmann/TML-Database trae `level` REAL por partido ('250'/'500'/'M'
Masters/'G' Grand Slam/'F' ATP Finals/'D' Davis Cup/'A' exhibición) --
TennisExplorer NUNCA expone esa categoría en ningún lado de su HTML
(verificado, no hay fuente propia). Pero SÍ trae el nombre del torneo,
y muchos torneos reales aparecen en AMBAS fuentes (mismo evento, año) --
se puede resolver `level` por transitividad: mismo (año, torneo
normalizado) -> mismo nivel real.

Verificado en vivo antes de implementar (2026-08-09): de 409,143 filas
TennisExplorer sin `level` pero con torneo, 261,157 (63.8%) tienen su
torneo también presente en Sackmann tras normalizar -- ganancia real
grande, no marginal."""

from __future__ import annotations

from typing import Any

from database.db import init_db_historical
from database.historical.tennis.models import MatchHistory

# Mismo criterio de normalización que
# `wta_favorite_surface_winrate_filter_backtest.py::_normalize_tournament_name`
# (quita sufijos de tour/nivel + tokens numéricos sueltos de edición) --
# no se importa de ahí a propósito (evitar que un módulo ATP dependa de
# uno nombrado "wta_*"), es la misma lógica simple, duplicarla acá es
# más claro que una dependencia cruzada rara.
_DROP_TOKENS = {"WTA", "ITF", "CHALLENGER"}


def _normalize_tournament_name(raw: str | None) -> str | None:
    if not raw:
        return None
    tokens = [t for t in raw.strip().upper().split(" ") if t]
    kept = [t for t in tokens if t not in _DROP_TOKENS and not t.isdigit()]
    if not kept:
        return None
    return " ".join(kept)


# Prioridad real de categoría ATP -- usada SOLO para resolver colisiones
# cuando 2+ torneos reales distintos normalizan a la MISMA clave
# (ej. "Rome" Masters1000 vs "Rome 4 challenger" -> ambos normalizan a
# "ROME" -- se conserva el nivel de MAYOR jerarquía real, mismo criterio
# de no-degradación que la versión WTA). Orden real aproximado por
# prestigio/premio: Grand Slam > ATP Finals > Masters1000 > 500 > 250 >
# Davis Cup (formato de equipos, no comparable directo) > exhibición.
_ATP_LEVEL_PRIORITY = {"G": 6, "F": 5, "M": 4, "500": 3, "250": 2, "D": 1, "A": 0}


def build_level_index_from_sackmann() -> dict[tuple[int, str], str]:
    """{(year, normalized_tournament_name): level} -- cargado desde las
    filas `source="sackmann"` de `MatchHistory` (ya tienen `level` real
    embebido, no hace falta ninguna fuente externa nueva)."""
    init_db_historical()
    index: dict[tuple[int, str], str] = {}
    rows = (
        MatchHistory
        .select(MatchHistory.tournament, MatchHistory.level, MatchHistory.match_date)
        .where(
            (MatchHistory.source == "sackmann")
            & (MatchHistory.tournament.is_null(False))
            & (MatchHistory.level.is_null(False))
        )
    )
    for r in rows:
        norm = _normalize_tournament_name(r.tournament)
        if norm is None or not r.match_date:
            continue
        try:
            year = int(r.match_date[:4])
        except (ValueError, TypeError):
            continue
        key = (year, norm)
        if key not in index or _ATP_LEVEL_PRIORITY.get(r.level, -1) > _ATP_LEVEL_PRIORITY.get(index[key], -1):
            index[key] = r.level
    return index


def run_backfill() -> dict[str, Any]:
    """Completa `level` en filas `source="tennisexplorer"` que tengan
    torneo pero no nivel, usando el índice derivado de Sackmann.
    Fail-closed real por fila: si el torneo no normaliza a nada, o el
    (año, torneo normalizado) no está en el índice, la fila queda sin
    tocar (nunca se adivina un nivel)."""
    init_db_historical()
    level_index = build_level_index_from_sackmann()

    rows = list(
        MatchHistory
        .select(MatchHistory.id, MatchHistory.tournament, MatchHistory.match_date)
        .where(
            (MatchHistory.source == "tennisexplorer")
            & (MatchHistory.level.is_null(True))
            & (MatchHistory.tournament.is_null(False))
        )
    )
    to_update: list[tuple[int, str]] = []
    for r in rows:
        norm = _normalize_tournament_name(r.tournament)
        if norm is None or not r.match_date:
            continue
        try:
            year = int(r.match_date[:4])
        except (ValueError, TypeError):
            continue
        level = level_index.get((year, norm))
        if level is not None:
            to_update.append((r.id, level))

    with MatchHistory._meta.database.atomic():
        for row_id, level in to_update:
            MatchHistory.update(level=level).where(MatchHistory.id == row_id).execute()

    return {
        "rows_checked": len(rows), "rows_resolved": len(to_update),
        "level_index_size": len(level_index),
    }


if __name__ == "__main__":
    print(run_backfill())
