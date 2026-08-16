"""Tennis Abstract (tennisabstract.com) — Elo ratings
reales, actualizados semanalmente, por jugador individual (NO por bucket
de ranking como el sistema propio de Kairos en `tennis_markov.py`).

CONTEXTO (2026-07-31, investigación a pedido explícito del usuario):
- `atp_elo_ratings.html`/`wta_elo_ratings.html`: Elo general de carrera +
  Elo específico por superficie (hElo=Hard, cElo=Clay, gElo=Grass),
  actualizado ~semanalmente (verificado en vivo: última actualización
  real 2026-07-27). Responde sin proxy (HTTP directo, sin bloqueo).
- `atp_season_yelo_ratings.html`/`wta_season_yelo_ratings.html`: "yElo",
  Elo de la temporada EN CURSO (no de carrera completa) + récord W/L de
  la temporada -- candidato a reemplazar el win-rate naive de
  `_blend_recent_form` en TennisFModel por algo metodológicamente más
  sólido.

Distinto de `match_charting_scraper.py` (mismo autor, pero datos de
puntos/saque por partido cargado, no Elo) y de `tennisexplorer_scraper.py`
(superficie por partido individual, no Elo).

Este módulo SOLO hace scraping crudo (fetch + parseo de tabla HTML con
BeautifulSoup) -- la superficie pública de llamada vive en
`outer_apis/sports/tennis/tennisabstract_elo.py`.

Fail-closed en cada paso: cualquier fallo de red/parseo devuelve [],
nunca fabrica una fila."""

from __future__ import annotations

from typing import Any

import requests
from bs4 import BeautifulSoup

_BASE_URL = "http://www.tennisabstract.com/reports"
_REQUEST_TIMEOUT_S = 15
_USER_AGENT = "Mozilla/5.0 (compatible; KairosResearchBot/1.0)"

_ELO_URLS = {"ATP": f"{_BASE_URL}/atp_elo_ratings.html", "WTA": f"{_BASE_URL}/wta_elo_ratings.html"}
_YELO_URLS = {"ATP": f"{_BASE_URL}/atp_season_yelo_ratings.html", "WTA": f"{_BASE_URL}/wta_season_yelo_ratings.html"}


def _fetch_table(url: str) -> list[list[str]] | None:
    """Fail-closed: None si la request falla, el status no es 200, o no
    se encuentra la tabla `#reportable` esperada."""
    try:
        resp = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=_REQUEST_TIMEOUT_S)
        if resp.status_code != 200:
            return None
    except requests.RequestException:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", id="reportable")
    if table is None:
        return None

    rows = table.find_all("tr")
    return [[td.get_text(strip=True) for td in tr.find_all("td")] for tr in rows[1:]]  # [0] es el header


def _parse_float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def get_elo_ratings(tour: str) -> list[dict[str, Any]]:
    """Elo de carrera + por superficie, real, para todo el tour.

    Returns:
        Lista de {"player", "elo", "h_elo", "c_elo", "g_elo", "atp_rank"}
        (h/c/g_elo pueden ser None si esa columna no parseó -- fail-closed
        por campo, no descarta la fila entera por un campo faltante ya
        que `elo` general es el dato mínimo indispensable).
        Fail-closed: [] si el tour es inválido o el fetch/parseo falla."""
    tour = tour.upper()
    if tour not in _ELO_URLS:
        return []

    rows = _fetch_table(_ELO_URLS[tour])
    if rows is None:
        return []

    result: list[dict[str, Any]] = []
    for cells in rows:
        # Layout confirmado en vivo 2026-07-31 (17 columnas, con separadores vacíos):
        # [rank, player, age, elo, '', hElo_rank, hElo, cElo_rank, cElo,
        #  gElo_rank, gElo, '', peak_elo, peak_month, '', atp_rank, log_diff]
        if len(cells) < 16:
            continue
        player = cells[1].replace("\xa0", " ").strip()
        elo = _parse_float(cells[3])
        if not player or elo is None:
            continue
        result.append({
            "player": player,
            "elo": elo,
            "h_elo": _parse_float(cells[6]),
            "c_elo": _parse_float(cells[8]),
            "g_elo": _parse_float(cells[10]),
            "atp_rank": _parse_int(cells[15]),
        })

    return result


def get_season_yelo(tour: str) -> list[dict[str, Any]]:
    """Elo de la temporada en curso + récord W/L, real, para todo el tour.

    Returns:
        Lista de {"player", "yelo", "wins", "losses"}.
        Fail-closed: [] si el tour es inválido o el fetch/parseo falla."""
    tour = tour.upper()
    if tour not in _YELO_URLS:
        return []

    rows = _fetch_table(_YELO_URLS[tour])
    if rows is None:
        return []

    result: list[dict[str, Any]] = []
    for cells in rows:
        # Layout confirmado en vivo 2026-07-31: [rank, player, wins, losses, yelo]
        if len(cells) < 5:
            continue
        player = cells[1].replace("\xa0", " ").strip()
        yelo = _parse_float(cells[4])
        wins = _parse_int(cells[2])
        losses = _parse_int(cells[3])
        if not player or yelo is None:
            continue
        result.append({"player": player, "yelo": yelo, "wins": wins, "losses": losses})

    return result
