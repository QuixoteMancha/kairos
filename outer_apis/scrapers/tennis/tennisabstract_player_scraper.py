"""Tennis Abstract — datos POR JUGADOR (distinto de `tennisabstract_scraper.py`,
que trae los reportes agregados de Elo/yElo para todo el tour). Cada
jugador/a tiene un único archivo JS con TODAS sus stats
(`jsfrags/{Nombre}.js`, sin espacios, ej. "JannikSinner.js"), servido sin
proxy.

CONTEXTO (2026-07-31, investigación a pedido explícito del usuario --
"es posible sacar todas las stats de tennis abstract?"): el archivo trae
~15 secciones (career-splits, head-to-heads, recent-results, point-by-
point Match Charting breakdowns, serve-speed, year-end-rankings, etc.).
Este módulo SOLO parsea las 2 de mayor valor real para el modelo de
Kairos hoy:

  - `career-splits`: win% de PARTIDO real (no proxy de Elo) por
    superficie, sobre la carrera completa -- muestra mucho más grande
    que nuestro `surface_form.py` (6 meses vía TennisExplorer) o que la
    calibración de p_serve por bucket (Match Charting Project). También
    trae hold%/break%/ace%/DF%/1st%/2nd% reales por superficie.
  - `head-to-heads`: récord real cabeza-a-cabeza contra rivales
    FRECUENTES (no todos, solo los que tienen historial significativo),
    con stats de esa rivalidad puntual (DR, serve stats en esos partidos).

Secciones identificadas pero DELIBERADAMENTE NO parseadas todavía
(backlog, ver memoria kairos-tennisabstract-elo-integration-2026-07-31):
`recent-results` (redundante con TennisExplorer, que ya cubre esto),
`mcp-serve`/`mcp-return`/`mcp-rally`/`mcp-tactics`/`pbp-*` (Match
Charting Project punto-por-punto, nicho), `serve-speed`, `year-end-
rankings`, `tour-years`, `chall-years`, `winners-errors`, `recent-finals`,
`recent-events`.

Fail-closed en cada paso: cualquier fallo de red/parseo devuelve [],
nunca fabrica una fila."""

from __future__ import annotations

import re
from typing import Any

import requests

_BASE_URL = "https://www.tennisabstract.com/jsfrags"
_REQUEST_TIMEOUT_S = 20
_USER_AGENT = "Mozilla/5.0 (compatible; KairosResearchBot/1.0)"

# Splits reales de superficie -- el resto de las filas de career-splits
# (Grand Slams/Masters/Other Tours/Best of 5/etc.) se descartan acá,
# no son superficie.
_SURFACE_SPLITS = {"Hard", "Clay", "Grass"}

_TAG_RE = re.compile(r"<[^>]+>")
_TR_RE = re.compile(r"<tr>(.*?)</tr>", re.DOTALL)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL)


def _slug_for_player(player_name: str) -> str:
    """"Jannik Sinner" -> "JannikSinner" (sin espacios, sin tildes)."""
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", player_name)
    ascii_name = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Za-z]", "", ascii_name)


def _clean_cell(raw: str) -> str:
    return _TAG_RE.sub("", raw).strip()


def _fetch_player_js(player_name: str) -> str | None:
    slug = _slug_for_player(player_name)
    if not slug:
        return None
    url = f"{_BASE_URL}/{slug}.js"
    try:
        resp = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=_REQUEST_TIMEOUT_S)
        if resp.status_code != 200:
            return None
        return resp.text
    except requests.RequestException:
        return None


def _extract_section(content: str, section_id: str) -> str | None:
    """Extrae el HTML entre `id="{section_id}-h"` y el próximo `<h1` (o
    fin de archivo si es la última sección)."""
    start_marker = f'id="{section_id}-h"'
    start = content.find(start_marker)
    if start == -1:
        return None
    end = content.find("<h1", start + len(start_marker))
    return content[start:end] if end != -1 else content[start:]


def _parse_float_pct(value: str) -> float | None:
    """"81.5%" -> 0.815. Fail-closed: None si no matchea."""
    m = re.match(r"^(-?\d+(?:\.\d+)?)%$", value.strip())
    return float(m.group(1)) / 100.0 if m else None


def _parse_int(value: str) -> int | None:
    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return None


def get_career_splits_by_surface(player_name: str) -> list[dict[str, Any]]:
    """Win% de partido real por superficie, carrera completa.

    Returns:
        Lista de {"surface", "matches", "wins", "losses", "win_pct",
        "hold_pct", "break_pct", "ace_pct", "df_pct"}.
        Fail-closed: [] si el jugador no se encuentra o el fetch/parseo
        falla."""
    content = _fetch_player_js(player_name)
    if content is None:
        return []

    section = _extract_section(content, "career-splits")
    if section is None:
        return []

    result: list[dict[str, Any]] = []
    for tr_html in _TR_RE.findall(section):
        cells = [_clean_cell(c) for c in _TD_RE.findall(tr_html)]
        if len(cells) < 16 or cells[0] not in _SURFACE_SPLITS:
            continue
        result.append({
            "surface": cells[0],
            "matches": _parse_int(cells[1]),
            "wins": _parse_int(cells[2]),
            "losses": _parse_int(cells[3]),
            "win_pct": _parse_float_pct(cells[4]),
            "hold_pct": _parse_float_pct(cells[12]),
            "break_pct": _parse_float_pct(cells[13]),
            "ace_pct": _parse_float_pct(cells[14]),
            "df_pct": _parse_float_pct(cells[15]),
        })

    return result


def get_head_to_head(player_name: str) -> list[dict[str, Any]]:
    """Récord real cabeza-a-cabeza contra rivales frecuentes.

    Returns:
        Lista de {"opponent", "meetings", "wins", "losses", "win_pct"}.
        Fail-closed: [] si el jugador no se encuentra o el fetch/parseo
        falla."""
    content = _fetch_player_js(player_name)
    if content is None:
        return []

    section = _extract_section(content, "head-to-heads")
    if section is None:
        return []

    result: list[dict[str, Any]] = []
    for tr_html in _TR_RE.findall(section):
        cells_raw = _TD_RE.findall(tr_html)
        if len(cells_raw) < 5:
            continue
        meetings = _parse_int(_clean_cell(cells_raw[0]))
        opponent_raw = _clean_cell(cells_raw[1])
        opponent = re.sub(r"\s*\[[A-Z]{2,3}\]\s*$", "", opponent_raw).strip()
        wins = _parse_int(_clean_cell(cells_raw[2]))
        losses = _parse_int(_clean_cell(cells_raw[3]))
        win_pct = _parse_float_pct(_clean_cell(cells_raw[4]))
        if not opponent or meetings is None:
            continue
        result.append({
            "opponent": opponent, "meetings": meetings,
            "wins": wins, "losses": losses, "win_pct": win_pct,
        })

    return result
