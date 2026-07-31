"""Facade DB-first para career-splits (win% real por superficie) y
head-to-head reales de Tennis Abstract -- ver
outer_apis/scrapers/tennis/tennisabstract_player_scraper.py.

Frescura POR JUGADOR (2026-07-31, pedido explícito del usuario: "solo
cuando se necesiten actualizar los datos de algún player"), no una tabla
global cacheada de una vez para todos -- cada jugador tiene su propio
reloj de 1 semana, así que un scan que solo necesita a 2 jugadores
puntuales no dispara un fetch de nadie más."""

from __future__ import annotations

from typing import Any

from database.historical.tennis.writer import (
    get_current_career_splits, save_tennisabstract_career_splits,
    get_current_tennisabstract_h2h, save_tennisabstract_h2h,
)
from outer_apis.scrapers.tennis import tennisabstract_player_scraper


def _fetch_career_splits(player_name: str) -> list[dict[str, Any]]:
    cached = get_current_career_splits(player_name)
    if cached is not None:
        return cached

    try:
        rows = tennisabstract_player_scraper.get_career_splits_by_surface(player_name)
    except Exception:
        rows = []
    if rows:
        save_tennisabstract_career_splits(player_name, rows)
    return rows


def _fetch_h2h(player_name: str) -> list[dict[str, Any]]:
    cached = get_current_tennisabstract_h2h(player_name)
    if cached is not None:
        return cached

    try:
        rows = tennisabstract_player_scraper.get_head_to_head(player_name)
    except Exception:
        rows = []
    if rows:
        save_tennisabstract_h2h(player_name, rows)
    return rows


def get_career_win_pct_by_surface(player_name: str, surface: str) -> dict[str, Any] | None:
    """Win% de partido REAL (no proxy de Elo) de `player_name` en
    `surface`, sobre su carrera completa. Fail-closed: None si `surface`
    es None/vacía, el jugador no tiene esa superficie en su historial, o
    el fetch falla."""
    if not surface:
        return None
    splits = _fetch_career_splits(player_name)
    for s in splits:
        if s.get("surface") == surface:
            return s
    return None


def get_h2h_vs(player_name: str, opponent_name: str) -> dict[str, Any] | None:
    """Récord real cabeza-a-cabeza de `player_name` contra `opponent_name`,
    SI figuran entre los rivales frecuentes de Tennis Abstract (no todos
    los rivales aparecen -- solo los de historial significativo). Match
    de nombre exacto normalizado (sin fuzzy -- evita falsos positivos
    entre apellidos parecidos en una lista corta de rivales).
    Fail-closed: None si no está en la lista o el fetch falla."""
    import re
    import unicodedata

    def _norm(name: str) -> str:
        nfkd = unicodedata.normalize("NFKD", name)
        ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
        return re.sub(r"[^a-z0-9 ]", " ", ascii_str.lower()).strip()

    target = _norm(opponent_name)
    h2h = _fetch_h2h(player_name)
    for row in h2h:
        if _norm(row["opponent"]) == target:
            return row
    return None
