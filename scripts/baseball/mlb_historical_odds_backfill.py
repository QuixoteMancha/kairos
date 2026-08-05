"""Backfill de odds PRE-PARTIDO reales de MLB (SportsbookReviewsOnline,
2010-2021) -- 2026-08-05, pedido explícito del usuario: "descarguemos
las odds históricas pre-match de los sitios que encontraste, por
scraper y guardémosla en la DB de la mini-pc".

Fetch de red DELIBERADO, fuera del loop de análisis -- mismo criterio
DB-first que el resto del repo (`tml_database_local_backfill.py`,
`mlb_local_backfill.py`): cualquier backtest futuro debe leer
`MlbHistoricalOdds` de la DB, nunca tocar la red.

Uso: `python3 -m scripts.baseball.mlb_historical_odds_backfill [start_year] [end_year]`
(default: rango completo real 2010-2021, ver
`sportsbookreviewsonline_scraper.MIN_YEAR`/`MAX_YEAR`)."""

from __future__ import annotations

import sys
from datetime import datetime, timezone

from database.db import init_db_historical
from database.historical.baseball import MlbHistoricalOdds
from outer_apis.scrapers.baseball import sportsbookreviewsonline_scraper as scraper


def backfill_year(year: int) -> dict[str, int]:
    """Fetch real de red para UNA temporada completa, persistido con
    (date, home_team, away_team) como clave real de dedup/idempotencia
    (reemplaza la temporada completa en cada corrida, para poder
    refrescar sin duplicar)."""
    init_db_historical()
    odds = scraper.get_year_odds(year)
    now = datetime.now(timezone.utc)

    rows = [{
        "date": o["date"], "home_team": o["home_team"], "away_team": o["away_team"],
        "home_moneyline_open": o.get("home_moneyline_open"), "home_moneyline_close": o.get("home_moneyline_close"),
        "away_moneyline_open": o.get("away_moneyline_open"), "away_moneyline_close": o.get("away_moneyline_close"),
        "home_implied_prob_open": o.get("home_implied_prob_open"), "home_implied_prob_close": o.get("home_implied_prob_close"),
        "away_implied_prob_open": o.get("away_implied_prob_open"), "away_implied_prob_close": o.get("away_implied_prob_close"),
        "source": "sportsbookreviewsonline", "fetched_at": now,
    } for o in odds]

    with MlbHistoricalOdds._meta.database.atomic():
        MlbHistoricalOdds.delete().where(
            (MlbHistoricalOdds.date >= f"{year:04d}-01-01") & (MlbHistoricalOdds.date <= f"{year:04d}-12-31")
        ).execute()
        if rows:
            # duplicados exactos reales dentro del mismo año (mismo
            # partido listado 2 veces) se descartan en vez de fallar el
            # backfill completo -- mismo criterio que TML-Database.
            MlbHistoricalOdds.insert_many(rows).on_conflict_ignore().execute()

    return {"year": year, "fetched": len(odds), "inserted": len(rows)}


def run_backfill(start_year: int = scraper.MIN_YEAR, end_year: int = scraper.MAX_YEAR) -> list[dict[str, int]]:
    results = []
    for year in range(start_year, end_year + 1):
        result = backfill_year(year)
        results.append(result)
        print(f"[mlb_historical_odds_backfill] {result}")
    return results


if __name__ == "__main__":
    start = int(sys.argv[1]) if len(sys.argv) > 1 else scraper.MIN_YEAR
    end = int(sys.argv[2]) if len(sys.argv) > 2 else scraper.MAX_YEAR
    run_backfill(start, end)
