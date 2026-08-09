"""Backfill UNIFICADO de `MatchHistory.level` (y `tour` como bonus) para
filas `source="tennisexplorer"` -- 2026-08-09, pedido explícito del
usuario: "unificarlos y añadir el scraping de todos los datos
faltantes... el level de wta" + "hay que tener un mapa de torneos, que
indique el level" (primera versión de este script recalculaba el
catálogo ATP en memoria en cada corrida; ahora lee de `AtpTournamentLevel`,
tabla persistente/inspeccionable, mismo patrón que `WtaTournamentLevel`).

Dos catálogos, cero requests de red nuevos (ambos ya existen en la DB
como tablas propias):

  - ATP: `AtpTournamentLevel` (derivada de Sackmann por voto de mayoría,
    poblada por `atp_tournament_level_backfill.py`).
  - WTA: `WtaTournamentLevel` (API oficial WTA, 17,902 torneos reales,
    poblada por `wta_tournament_level_backfill.py`).

TennisExplorer no expone la categoría del torneo en ningún lado de su
HTML -- pero SÍ trae el nombre, y la mayoría de los torneos reales
aparecen también en uno de los 2 catálogos de arriba (mismo evento,
mismo año) -- se resuelve por transitividad: mismo (año, torneo
normalizado) -> mismo nivel real.

Selección de catálogo por fila:
  - `tour="ATP"` conocido -> solo catálogo ATP.
  - `tour="WTA"` conocido -> solo catálogo WTA.
  - `tour` desconocido (`None`) -> se prueban AMBOS catálogos. Si
    resuelve en uno solo, se usa ese nivel Y se completa `tour` como
    bonus (match inequívoco de torneo real = tour real, sin adivinar).
    Si resuelve en AMBOS con años/niveles distintos (colisión real de
    nombre entre un evento ATP y uno WTA el mismo año -- rara pero
    posible, ej. "United Cup" es mixto), fail-closed: no se toca ni
    `level` ni `tour`, nunca se adivina cuál corresponde."""

from __future__ import annotations

from typing import Any

from database.db import init_db_historical
from database.historical.tennis.models import MatchHistory, WtaTournamentLevel, AtpTournamentLevel

# Mismo criterio de normalización que
# `wta_favorite_surface_winrate_filter_backtest.py::_normalize_tournament_name`
# (quita sufijos de tour/nivel + tokens numéricos sueltos de edición) --
# no se importa de ahí a propósito (evitar que este módulo dependa de
# uno nombrado "wta_*" cuando ahora también cubre ATP), duplicar esta
# lógica simple es más claro que una dependencia cruzada rara.
_DROP_TOKENS = {"WTA", "ITF", "CHALLENGER"}


def _normalize_tournament_name(raw: str | None) -> str | None:
    if not raw:
        return None
    tokens = [t for t in raw.strip().upper().split(" ") if t]
    kept = [t for t in tokens if t not in _DROP_TOKENS and not t.isdigit()]
    if not kept:
        return None
    return " ".join(kept)


# Prioridad real de categoría WTA -- valores tomados de
# `wta_tournament_level_backfill.py::_LEVEL_PRIORITY` (no se importa
# para no acoplar el orden de carga de módulos, son constantes fijas).
_WTA_LEVEL_PRIORITY = {
    "Grand Slam": 6, "WTA 1000": 5, "Premier Mandatory": 5, "Premier 5": 5,
    "WTA 500": 4, "Premier": 4, "Finals": 4,
    "WTA 250": 3, "International": 3,
    "WTA 125": 2, "125K": 2,
    "ITF": 1,
}


def build_level_index_from_sackmann() -> dict[tuple[int, str], str]:
    """{(year, normalized_tournament_name): level} -- catálogo ATP,
    leído directo de `AtpTournamentLevel` (tabla persistente, poblada
    aparte vía `atp_tournament_level_backfill.py` con voto de mayoría
    real -- ya NO se recalcula acá cada vez). El nombre de la función
    se mantiene por compatibilidad con el resto del módulo/tests."""
    init_db_historical()
    index: dict[tuple[int, str], str] = {}
    for r in AtpTournamentLevel.select():
        if r.level is None:
            continue
        index[(r.year, r.tournament_name)] = r.level
    return index


def build_level_index_from_wta_catalog() -> dict[tuple[int, str], str]:
    """{(year, normalized_tournament_name): level} -- catálogo WTA,
    derivado de `WtaTournamentLevel` (API oficial WTA, ya backfilleada
    aparte vía `wta_tournament_level_backfill.py`)."""
    init_db_historical()
    index: dict[tuple[int, str], str] = {}
    for r in WtaTournamentLevel.select():
        norm = _normalize_tournament_name(r.tournament_name)
        if norm is None:
            continue
        key = (r.year, norm)
        if key not in index or _WTA_LEVEL_PRIORITY.get(r.level, 0) > _WTA_LEVEL_PRIORITY.get(index[key], 0):
            index[key] = r.level
    return index


def run_backfill() -> dict[str, Any]:
    """Completa `level` (y `tour` como bonus, ver docstring del módulo)
    en filas `source="tennisexplorer"` que tengan torneo pero no nivel,
    usando los 2 índices. Fail-closed real por fila: si el torneo no
    normaliza a nada, si el (año, torneo normalizado) no está en NINGÚN
    índice, o si está en AMBOS con resultado ambiguo, la fila queda sin
    tocar (nunca se adivina un nivel ni un tour)."""
    init_db_historical()
    atp_index = build_level_index_from_sackmann()
    wta_index = build_level_index_from_wta_catalog()

    rows = list(
        MatchHistory
        .select(MatchHistory.id, MatchHistory.tournament, MatchHistory.match_date, MatchHistory.tour)
        .where(
            (MatchHistory.source == "tennisexplorer")
            & (MatchHistory.level.is_null(True))
            & (MatchHistory.tournament.is_null(False))
        )
    )
    resolved_atp = resolved_wta = resolved_ambiguous_skipped = 0
    tour_also_filled = 0
    to_update: list[tuple[int, str, str | None]] = []  # (id, level, tour_if_new)

    for r in rows:
        norm = _normalize_tournament_name(r.tournament)
        if norm is None or not r.match_date:
            continue
        try:
            year = int(r.match_date[:4])
        except (ValueError, TypeError):
            continue
        key = (year, norm)

        if r.tour == "ATP":
            level = atp_index.get(key)
            if level is not None:
                to_update.append((r.id, level, None))
                resolved_atp += 1
            continue
        if r.tour == "WTA":
            level = wta_index.get(key)
            if level is not None:
                to_update.append((r.id, level, None))
                resolved_wta += 1
            continue

        # tour desconocido -- probar ambos catálogos.
        atp_level = atp_index.get(key)
        wta_level = wta_index.get(key)
        if atp_level is not None and wta_level is not None:
            resolved_ambiguous_skipped += 1
            continue  # colisión real de nombre entre evento ATP y WTA -- fail-closed
        if atp_level is not None:
            to_update.append((r.id, atp_level, "ATP"))
            resolved_atp += 1
            tour_also_filled += 1
        elif wta_level is not None:
            to_update.append((r.id, wta_level, "WTA"))
            resolved_wta += 1
            tour_also_filled += 1

    with MatchHistory._meta.database.atomic():
        for row_id, level, tour in to_update:
            fields = {"level": level}
            if tour is not None:
                fields["tour"] = tour
            MatchHistory.update(**fields).where(MatchHistory.id == row_id).execute()

    return {
        "rows_checked": len(rows),
        "rows_resolved": len(to_update),
        "resolved_atp": resolved_atp,
        "resolved_wta": resolved_wta,
        "resolved_ambiguous_skipped": resolved_ambiguous_skipped,
        "tour_also_filled": tour_also_filled,
        "atp_level_index_size": len(atp_index),
        "wta_level_index_size": len(wta_index),
    }


if __name__ == "__main__":
    print(run_backfill())
