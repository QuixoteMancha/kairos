"""Backfill de temporadas reales de MLB a la tabla local
`MlbGameHistory` -- 2026-08-04, pedido explícito del usuario tras notar
que `TeamMatchHistory` (usada por el Elo en vivo) es solo una ventana
rodante de 45 días, no un histórico real para backtesting.

Mismo criterio DB-first ya aplicado a TML-Database (tenis): el fetch de
red (`mlb_stats_scraper.get_games_in_range` + `get_linescore`, 2
requests por partido) se hace SOLO acá, de forma deliberada y fuera del
loop de análisis (`scripts/mlb_comeback_analysis.py` debe leer de la DB
local vía `collect_games_from_db`, nunca de la red directamente).

Uso: `python3 -m scripts.mlb_local_backfill [start_year] [end_year]`
Default: 2015-2026. Idempotente por año: si ya hay partidos de un año
en la DB, se salta (salvo el año en curso, que siempre se refresca
porque sigue jugándose)."""

from __future__ import annotations

import json
import sys
import time
from datetime import date, datetime, timezone

from database.db import init_db_historical
from database.historical.baseball import MlbGameHistory
from outer_apis.scrapers.baseball import mlb_stats_scraper

_PAUSE_BETWEEN_REQUESTS_S = 0.3
_REGULAR_SEASON_START = "-03-15"  # spring training termina, temporada regular arranca ~fin de marzo/inicio abril; margen amplio
_REGULAR_SEASON_END = "-11-15"    # cubre postemporada/World Series real


def _year_has_cache(year: int) -> bool:
    init_db_historical()
    return MlbGameHistory.select().where(MlbGameHistory.date.startswith(str(year))).exists()


def backfill_year(year: int) -> dict[str, int]:
    """Fetch real de red para UN año completo (temporada regular +
    postemporada), persistido con `game_pk` como clave real de
    dedup/idempotencia (reemplaza el año completo en cada corrida, para
    poder refrescar el año en curso sin duplicar)."""
    init_db_historical()
    start = f"{year}{_REGULAR_SEASON_START}"
    end = f"{year}{_REGULAR_SEASON_END}"

    games = mlb_stats_scraper.get_games_in_range(start, end)
    rows = []
    for g in games:
        game_pk = g.get("game_pk")
        if game_pk is None:
            continue
        linescore = mlb_stats_scraper.get_linescore(game_pk)
        time.sleep(_PAUSE_BETWEEN_REQUESTS_S)
        rows.append({
            "game_pk": game_pk, "date": g["date"], "home_team": g["home_team"],
            "away_team": g["away_team"], "home_won": g["home_won"],
            "home_runs_final": (linescore or {}).get("home_runs_total"),
            "away_runs_final": (linescore or {}).get("away_runs_total"),
            "innings_json": json.dumps(linescore["innings"]) if linescore else None,
            "game_datetime_utc": g.get("game_datetime_utc"),
            "fetched_at": datetime.now(timezone.utc),
        })

    with MlbGameHistory._meta.database.atomic():
        MlbGameHistory.delete().where(MlbGameHistory.date.startswith(str(year))).execute()
        if rows:
            for i in range(0, len(rows), 400):  # margen bajo el límite de parámetros de SQLite
                MlbGameHistory.insert_many(rows[i:i + 400]).on_conflict_ignore().execute()

    return {"year": year, "games_saved": len(rows)}


def enrich_game_datetimes(start_year: int = 2015, end_year: int = 2026) -> dict[str, int]:
    """Enriquecimiento LIVIANO -- 2026-08-05, pedido explícito del
    usuario ("adapta el backfill de clima de tenis para MLB"): las
    filas ya backfilleadas (26,882 partidos, 2015-2026) no tienen
    `game_datetime_utc` porque ese campo se agregó DESPUÉS del backfill
    original. En vez de re-correr `run_backfill` completo (que
    re-pediría el linescore pesado de cada partido otra vez, sin
    necesidad -- las carreras/entradas ya están bien), esto solo vuelve
    a pedir el CALENDARIO (1 request por año, barato) y actualiza
    `game_datetime_utc` por `game_pk` en las filas que ya existen."""
    init_db_historical()
    updated = 0
    for year in range(start_year, end_year + 1):
        start = f"{year}{_REGULAR_SEASON_START}"
        end = f"{year}{_REGULAR_SEASON_END}"
        games = mlb_stats_scraper.get_games_in_range(start, end)
        with MlbGameHistory._meta.database.atomic():
            for g in games:
                game_pk = g.get("game_pk")
                dt = g.get("game_datetime_utc")
                if game_pk is None or not dt:
                    continue
                updated += (
                    MlbGameHistory.update(game_datetime_utc=dt)
                    .where((MlbGameHistory.game_pk == game_pk) & (MlbGameHistory.game_datetime_utc.is_null()))
                    .execute()
                )
    return {"rows_updated": updated}


def run_backfill(start_year: int = 2015, end_year: int = 2026, force: bool = False) -> dict[str, int]:
    current_year = date.today().year
    years_ok = years_skipped_cached = 0
    total_games = 0

    for year in range(start_year, end_year + 1):
        if not force and year != current_year and _year_has_cache(year):
            years_skipped_cached += 1
            continue
        result = backfill_year(year)
        years_ok += 1
        total_games += result["games_saved"]

    return {"years_ok": years_ok, "years_skipped_cached": years_skipped_cached, "total_games_saved": total_games}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "enrich_datetimes":
        start = int(sys.argv[2]) if len(sys.argv) > 2 else 2015
        end = int(sys.argv[3]) if len(sys.argv) > 3 else 2026
        print(enrich_game_datetimes(start, end))
    else:
        start = int(sys.argv[1]) if len(sys.argv) > 1 else 2015
        end = int(sys.argv[2]) if len(sys.argv) > 2 else 2026
        print(run_backfill(start, end))
