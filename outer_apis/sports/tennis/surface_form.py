"""Forma reciente por superficie — win rate de un/a jugador/a en las
últimas 52 semanas, filtrado a la superficie específica del próximo
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

from database.historical.tennis.queries import get_player_matches

LOOKBACK_DAYS = 365  # ventana móvil de 52 semanas
MIN_MATCHES_ON_SURFACE = 3  # fail-closed: menos partidos reales en esa superficie -> sin señal confiable
RECENT_FORM_MATCHES = 10
MIN_RECENT_FORM_MATCHES = 5
_DB_FRESHNESS_HOURS = 24.0

_TOUR_SEX = {"ATP": "man", "WTA": "woman"}


def _fetch_and_cache(player_name: str, tour: str | None) -> list[dict[str, Any]]:
    """DB-first: usa el historial fresco de DB si existe; si no,
    scrapea TennisExplorer para el año actual + el anterior (cubre la
    ventana de 52 semanas sin importar en qué mes del año estemos)
    y lo persiste. Fail-closed: [] si todo falla -- nunca fabrica
    partidos."""
    cutoff = date.today().fromordinal(date.today().toordinal() - LOOKBACK_DAYS)
    return get_player_matches(player_name, limit=None, since=cutoff)


def get_recent_form_by_surface(player_name: str, surface: str, tour: str | None = None) -> dict[str, Any] | None:
    """Win rate real de `player_name` en la superficie `surface`,
    restringido a las últimas 52 semanas (`LOOKBACK_DAYS`).

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

    matches = [row for row in _fetch_and_cache(player_name, tour) if row.get("surface") == surface]
    if len(matches) < MIN_MATCHES_ON_SURFACE:
        return None
    wins = sum(row.get("result") == "W" for row in matches)
    return {"win_rate": round(wins / len(matches), 4), "n_matches": len(matches)}


def get_recent_form(player_name: str, tour: str | None = None) -> dict[str, Any] | None:
    """Forma general de los últimos 10 partidos, con mínimo de 5."""
    matches = sorted(
        _fetch_and_cache(player_name, tour),
        key=lambda match: match.get("match_date") or "",
        reverse=True,
    )[:RECENT_FORM_MATCHES]
    if len(matches) < MIN_RECENT_FORM_MATCHES:
        return None
    wins = sum(1 for match in matches if match.get("result") == "W")
    return {"win_rate": round(wins / len(matches), 4), "n_matches": len(matches)}
