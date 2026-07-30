"""Forma reciente por superficie — win rate de un/a jugador/a en los
últimos ~6 meses, filtrado a la superficie específica del próximo
partido. Construido 2026-07-30 a pedido explícito del usuario, punto 3
del pedido "surface + set-sequence + form por superficie" para
TennisFModel.

Fuente: TennisExplorer.com (`outer_apis/scrapers/tennis/
tennisexplorer_scraper.py`), la única fuente encontrada con superficie
REAL por partido individual (Flashscore no la expone, ver memoria
kairos-tennis-surface-form-2026-07-30) y que además no necesita el proxy
de BrightData (que está caído en prod al momento de este cambio, hallazgo
reportado por separado).

DB-first (mismo patrón que outer_apis/sports/tennis/match_history.py):
antes de cualquier fetch en vivo, se consulta
`database/historical/tennis/writer.py::get_recent_surface_match_history`
-- solo si no hay datos frescos (<24h) se scrapea TennisExplorer, con
throttle incorporado en el scraper (punto 2 del pedido)."""

from __future__ import annotations

from datetime import date
from typing import Any

from database.historical.tennis.writer import (
    get_recent_surface_match_history, save_surface_match_history,
)
from outer_apis.scrapers.tennis import tennisexplorer_scraper

LOOKBACK_DAYS = 183  # ~6 meses, pedido explícito del usuario
MIN_MATCHES_ON_SURFACE = 3  # fail-closed: menos partidos reales en esa superficie -> sin señal confiable
_DB_FRESHNESS_HOURS = 24.0

_TOUR_SEX = {"ATP": "man", "WTA": "woman"}


def _fetch_and_cache(player_name: str, tour: str | None) -> list[dict[str, Any]]:
    """DB-first: usa el historial fresco de DB si existe; si no,
    scrapea TennisExplorer para el año actual + el anterior (cubre la
    ventana de 6 meses sin importar en qué mes del año estemos, ej. en
    enero los últimos 6 meses caen mayormente en el año calendario previo)
    y lo persiste. Fail-closed: [] si todo falla -- nunca fabrica
    partidos."""
    cached = get_recent_surface_match_history(
        player_name, max_age_hours=_DB_FRESHNESS_HOURS, lookback_days=LOOKBACK_DAYS,
    )
    if cached is not None:
        return cached

    sex = _TOUR_SEX.get(tour) if tour else None
    current_year = date.today().year
    matches = tennisexplorer_scraper.get_player_matches(
        player_name, years=[current_year, current_year - 1], sex=sex,
    )
    if matches:
        save_surface_match_history(player_name, matches)

    cutoff = date.today().toordinal() - LOOKBACK_DAYS
    return [
        m for m in matches
        if m.get("match_date") and date.fromisoformat(m["match_date"]).toordinal() >= cutoff
    ]


def get_recent_form_by_surface(player_name: str, surface: str, tour: str | None = None) -> dict[str, Any] | None:
    """Win rate real de `player_name` en la superficie `surface`,
    restringido a los últimos ~6 meses (`LOOKBACK_DAYS`).

    Returns:
        {"win_rate": float, "n_matches": int} o None si:
          - `surface` es None/vacío (superficie del partido desconocida,
            ver outer_apis/scrapers/tennis/tournament_surface.py), o
          - no hay al menos `MIN_MATCHES_ON_SURFACE` partidos reales del
            jugador en esa superficie específica dentro de la ventana, o
          - el fetch/scraping falla por completo.
        Fail-closed en los 3 casos -- nunca fabrica una tasa desde una
        muestra insuficiente o desde una superficie desconocida."""
    if not surface:
        return None

    matches = _fetch_and_cache(player_name, tour)
    surface_matches = [m for m in matches if m.get("surface") == surface]
    if len(surface_matches) < MIN_MATCHES_ON_SURFACE:
        return None

    wins = sum(1 for m in surface_matches if m.get("result") == "W")
    return {"win_rate": round(wins / len(surface_matches), 4), "n_matches": len(surface_matches)}
