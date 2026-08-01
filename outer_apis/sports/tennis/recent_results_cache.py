"""DB-first fetch-and-cache de `recent-results` (Tennis Abstract),
compartido entre `injury_risk.py` (retiros/walkovers) y
`tournament_momentum.py` (avance de rondas) -- ambos necesitan
exactamente los mismos datos crudos por jugador, factorizado acá 2026-08-01
para no duplicar la lógica de fetch/caché en 2 módulos."""

from __future__ import annotations

from typing import Any

from database.historical.tennis.writer import get_recent_results, save_recent_results
from outer_apis.scrapers.tennis import tennisabstract_player_scraper


def fetch_and_cache(player_name: str) -> list[dict[str, Any]]:
    cached = get_recent_results(player_name)
    if cached is not None:
        return cached

    try:
        rows = tennisabstract_player_scraper.get_recent_results(player_name)
    except Exception:
        rows = []
    if rows:
        save_recent_results(player_name, rows)
    return rows
