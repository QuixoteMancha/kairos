"""Backfill de TML-Database (1968-2026) a la tabla local
`TmlDatabaseMatch` -- 2026-08-04, pedido explícito del usuario ("el
backtesting se debe hacer con datos de base, nada de estar llamando a
fuentes en red a cada rato").

Corre el fetch de RED (una sola vez, deliberado, fuera del loop de
backtesting) y persiste TODOS los niveles de torneo (sin filtrar por
`TOUR_LEVELS_250_PLUS` -- el filtro se aplica al leer, no al guardar,
para no tener que re-fetchear si algún día se necesita otro nivel).
Idempotente: re-correr reemplaza los datos de cada año con la versión
más reciente del CSV real (útil sobre todo para el año en curso, que
sigue creciendo).

Uso: `python3 -m scripts.tml_database_local_backfill [start_year] [end_year]`
Default: 1968-2026 (todo el rango real de la fuente)."""

from __future__ import annotations

import sys
import time
from datetime import date

from outer_apis.scrapers.tennis.tml_database_scraper import (
    get_year_matches, save_matches_to_db, has_cached_year,
)

_PAUSE_BETWEEN_YEARS_S = 0.3


def run_backfill(start_year: int = 1968, end_year: int = 2026, force: bool = False) -> dict[str, int]:
    """Fetch real de red por año + persistencia en `TmlDatabaseMatch`.
    `force=False` (default) salta años que YA tienen cache local (para
    no re-fetchear todo el histórico cada vez que se agrega un año
    nuevo) -- pasar `force=True` para refrescar TODO el rango (útil
    para el año en curso, que cambia)."""
    years_ok = years_skipped_cached = years_failed = 0
    total_matches = 0

    current_year = date.today().year
    for year in range(start_year, end_year + 1):
        # el año EN CURSO sigue creciendo (partidos nuevos cada
        # semana) -- refrescarlo siempre, sin importar `force`,
        # cualquier otro año histórico ya cerrado es estable y se
        # respeta el cache salvo `force=True` explícito.
        if not force and year != current_year and has_cached_year(year):
            years_skipped_cached += 1
            continue

        matches = get_year_matches(year, levels=None)  # sin filtro de nivel -- se guarda TODO
        n = save_matches_to_db(year, matches)
        if n == 0 and matches == []:
            years_failed += 1
        else:
            years_ok += 1
            total_matches += n
        time.sleep(_PAUSE_BETWEEN_YEARS_S)

    return {
        "years_ok": years_ok, "years_skipped_cached": years_skipped_cached,
        "years_failed": years_failed, "total_matches_saved": total_matches,
    }


if __name__ == "__main__":
    start = int(sys.argv[1]) if len(sys.argv) > 1 else 1968
    end = int(sys.argv[2]) if len(sys.argv) > 2 else 2026
    result = run_backfill(start, end)
    print(result)
