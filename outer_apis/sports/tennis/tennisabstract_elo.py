"""Elo real por jugador (carrera + por superficie) y Elo de temporada
("yElo"), fuente Tennis Abstract (Jeff Sackmann) -- ver
outer_apis/scrapers/tennis/tennisabstract_scraper.py para el detalle de
scraping y por qué esto es un salto de calidad frente al sistema de
buckets de ranking que usa `models/volatility/sports/tennis/tennis_markov.py`.

DB-first (mismo patrón que el resto de outer_apis/sports/tennis/*):
`_TA_ELO_FRESHNESS_HOURS` (4 días, ver writer.py) antes de cualquier fetch
en vivo -- el sitio se actualiza semanalmente, no hace falta re-scrapear
más seguido.

Matching de nombres: Tennis Abstract usa nombres completos "Nombre
Apellido" (sin abreviar), igual que Kalshi (`player_a_name`/
`player_b_name` en outer_apis/kalshi/markets/tennis.py) -- match exacto
normalizado primero, fuzzy (difflib) como fallback para acentos/
variaciones menores. Fail-closed: sin match por encima del cutoff -> None,
nunca se adivina."""

from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Any

from database.historical.tennis.writer import (
    get_current_tennisabstract_elo, save_tennisabstract_elo_snapshot,
    get_current_tennisabstract_yelo, save_tennisabstract_yelo_snapshot,
)
from outer_apis.scrapers.tennis import tennisabstract_scraper

_FUZZY_MATCH_CUTOFF = 0.85


def _norm(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9 ]", " ", ascii_str.lower()).strip()


def _lookup(name: str, table: dict[str, Any]) -> Any | None:
    norm_to_key = {_norm(k): k for k in table}
    target = _norm(name)
    if target in norm_to_key:
        return table[norm_to_key[target]]
    matches = difflib.get_close_matches(target, list(norm_to_key.keys()), n=1, cutoff=_FUZZY_MATCH_CUTOFF)
    return table[norm_to_key[matches[0]]] if matches else None


def _fetch_elo_table(tour: str) -> dict[str, dict[str, Any]]:
    cached = get_current_tennisabstract_elo(tour)
    if cached is not None:
        return cached

    try:
        rows = tennisabstract_scraper.get_elo_ratings(tour)
    except Exception:
        rows = []
    if rows:
        save_tennisabstract_elo_snapshot(tour, rows)
    return {r["player"]: r for r in rows}


def _fetch_yelo_table(tour: str) -> dict[str, dict[str, Any]]:
    cached = get_current_tennisabstract_yelo(tour)
    if cached is not None:
        return cached

    try:
        rows = tennisabstract_scraper.get_season_yelo(tour)
    except Exception:
        rows = []
    if rows:
        save_tennisabstract_yelo_snapshot(tour, rows)
    return {r["player"]: r for r in rows}


def get_elo(player_name: str, tour: str, surface: str | None = None) -> float | None:
    """Elo real del jugador -- usa el específico de `surface` (hElo/cElo/
    gElo) si está disponible, si no cae al Elo general de carrera.
    Fail-closed: None si el tour es inválido, el jugador no matchea, o
    todo falla (nunca fabrica un Elo)."""
    if tour not in ("ATP", "WTA"):
        return None

    table = _fetch_elo_table(tour)
    if not table:
        return None

    row = _lookup(player_name, table)
    if row is None:
        return None

    surface_key = {"Hard": "h_elo", "Clay": "c_elo", "Grass": "g_elo"}.get(surface or "")
    if surface_key and row.get(surface_key) is not None:
        return row[surface_key]
    return row.get("elo")


def get_season_form(player_name: str, tour: str) -> dict[str, Any] | None:
    """yElo + récord W/L de la temporada en curso. Fail-closed: None si
    el tour es inválido, el jugador no matchea, o todo falla."""
    if tour not in ("ATP", "WTA"):
        return None

    table = _fetch_yelo_table(tour)
    if not table:
        return None

    return _lookup(player_name, table)


def elo_implied_win_prob(elo_a: float, elo_b: float) -> float:
    """Fórmula logística estándar de Elo: P(A gana) = 1 / (1 + 10^((Elo_B - Elo_A) / 400)).
    Misma constante 400 que ajedrez/Sackmann usan para esta escala de Elo."""
    return 1.0 / (1.0 + 10 ** ((elo_b - elo_a) / 400.0))
