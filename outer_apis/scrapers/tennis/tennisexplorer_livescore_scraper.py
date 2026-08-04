"""TennisExplorer.com -- score EN VIVO real, sin proxy (2026-08-02,
pedido explícito del usuario: "vamos a ver el fetch del live score y
stats para alimentar aún más la data del sistema" -- cierra el gap ya
documentado toda la sesión de que Kairos no tiene feed de score en vivo,
bloqueante para automatizar `strategies/set1_favorite_reaction.py`).

FUENTE: `https://www.tennisexplorer.com/matches/?type={atp,wta}-single`
-- verificado en vivo, sin proxy (200 OK, ya usado en el repo para
`tennisexplorer_scraper.py`, forma por superficie). Formato real de la
tabla de partidos del día (reverse-engineered 2026-08-02):

  Cada partido = 2 filas `<tr>` consecutivas (una por jugador), con:
    - `<td class="result">N</td>` -- sets ganados por ESE jugador hasta
      ahora (0, 1, o 2 si ya ganó el partido en best-of-3).
    - `<td class="score">G</td>` por cada set, uno por columna -- games
      ganados en ese set. Tiebreak real: `6<sup>7</sup>` (perdió el set
      6-7 en tiebreak) -- el número en `<sup>` es el score del tiebreak,
      NO otro set.
    - Indicador de partido EN VIVO real: presencia de
      `icon-tv.gif`/`Live streams` en la fila (torneos con transmisión) --
      NO es 100% infalible (partidos en vivo sin streaming no lo tienen),
      se complementa con "tiene scores parciales Y ningún jugador llegó
      a 2 sets todavía" como señal adicional de "en progreso".

Fail-closed en cada paso: fila sin ambos jugadores parseables, o sin
scores reales, se descarta -- nunca se fabrica un resultado."""

from __future__ import annotations

import re
from typing import Any

import requests

from outer_apis.scrapers.tennis._request_throttle import tennisexplorer_throttle

_MATCHES_URL = "https://www.tennisexplorer.com/matches/?type={tour_type}"
_REQUEST_TIMEOUT_S = 20
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

_TOUR_TYPE = {"ATP": "atp-single", "WTA": "wta-single"}

_ROW_RE = re.compile(r'<tr id="[^"]+"[^>]*>(.*?)</tr>', re.DOTALL)
_PLAYER_NAME_RE = re.compile(r'<td class="t-name"><a href="[^"]+">([^<]+)</a>')
_RESULT_RE = re.compile(r'<td class="result">(\d+)</td>')
_SCORE_CELL_RE = re.compile(r'<td class="score">(.*?)</td>')
_HAS_STREAMS_RE = re.compile(r"icon-tv\.gif|Live\s*streams")


def _fetch_matches_page(tour: str) -> str | None:
    """Throttle real (2026-08-04, pedido explícito -- "no overhittear
    los servers") compartido con `tennisexplorer_player_scraper._fetch`
    (mismo sitio, mismo presupuesto real)."""
    tour_type = _TOUR_TYPE.get(tour)
    if tour_type is None:
        return None
    tennisexplorer_throttle.wait()
    try:
        resp = requests.get(
            _MATCHES_URL.format(tour_type=tour_type),
            headers={"User-Agent": _USER_AGENT}, timeout=_REQUEST_TIMEOUT_S,
        )
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    return resp.text


def _parse_score_cell(raw: str) -> int | None:
    """'6' -> 6, '6<sup>7</sup>' -> 6 (el tiebreak en <sup> se
    descarta, no es un set aparte). '&nbsp;' (set no jugado) -> None."""
    cleaned = re.sub(r"<sup>.*?</sup>", "", raw).strip()
    if not cleaned or "nbsp" in cleaned:
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def _parse_player_row(row_html: str) -> dict[str, Any] | None:
    name_match = _PLAYER_NAME_RE.search(row_html)
    result_match = _RESULT_RE.search(row_html)
    if not name_match or not result_match:
        return None

    set_scores = [_parse_score_cell(c) for c in _SCORE_CELL_RE.findall(row_html)]
    return {
        "name": name_match.group(1).strip(),
        "sets_won": int(result_match.group(1)),
        "set_scores": set_scores,
        "has_live_stream": bool(_HAS_STREAMS_RE.search(row_html)),
    }


def get_live_matches(tour: str) -> list[dict[str, Any]]:
    """Partidos de HOY con al menos un set en curso/jugado (real,
    parciales o terminados) -- `tour`: "ATP" | "WTA".

    Returns: lista de {"player_a", "player_b", "sets_won_a",
    "sets_won_b", "set_scores_a", "set_scores_b", "is_finished",
    "has_live_stream"} -- `is_finished` = True si algún jugador ya llegó
    a 2 sets ganados (best-of-3 real, asume best-of-3 -- best-of-5 no
    aplica en ATP/WTA regular tour).

    Fail-closed: [] si el fetch falla o `tour` no es 'ATP'/'WTA'."""
    html = _fetch_matches_page(tour)
    if html is None:
        return []

    rows = [_parse_player_row(r) for r in _ROW_RE.findall(html)]
    valid_rows = [r for r in rows if r is not None]

    result: list[dict[str, Any]] = []
    i = 0
    while i < len(valid_rows) - 1:
        row_a, row_b = valid_rows[i], valid_rows[i + 1]
        # Solo tiene sentido parear filas consecutivas si AMBAS tienen
        # al menos algún set jugado (0-0 real de scores => todavía no
        # arrancó, no es "en vivo") -- fail-closed, no empareja al azar.
        if any(s is not None for s in row_a["set_scores"]) or any(s is not None for s in row_b["set_scores"]):
            result.append({
                "player_a": row_a["name"], "player_b": row_b["name"],
                "sets_won_a": row_a["sets_won"], "sets_won_b": row_b["sets_won"],
                "set_scores_a": row_a["set_scores"], "set_scores_b": row_b["set_scores"],
                "is_finished": row_a["sets_won"] >= 2 or row_b["sets_won"] >= 2,
                "has_live_stream": row_a["has_live_stream"] or row_b["has_live_stream"],
            })
            i += 2
        else:
            i += 1

    return result


def get_set1_winner(player_a: str, player_b: str, tour: str) -> str | None:
    """'a' | 'b' | None -- quién ganó el SET 1 real del partido EN VIVO
    entre `player_a`/`player_b` (matching por nombre exacto tal como
    aparece en la fuente -- fail-closed si no matchea, no usa fuzzy acá
    a propósito, para no confundir jugadores con apellidos parecidos en
    un contexto de score en vivo).

    Fail-closed: None si no se encuentra el partido, el set 1 todavía
    no terminó (algún score de set 1 es None), o el tour no es
    'ATP'/'WTA'."""
    matches = get_live_matches(tour)
    for m in matches:
        if {m["player_a"], m["player_b"]} != {player_a, player_b}:
            continue
        s1_a = m["set_scores_a"][0] if m["set_scores_a"] else None
        s1_b = m["set_scores_b"][0] if m["set_scores_b"] else None
        if s1_a is None or s1_b is None or s1_a == s1_b:
            return None
        winner_is_a = s1_a > s1_b
        if m["player_a"] == player_a:
            return "a" if winner_is_a else "b"
        return "b" if winner_is_a else "a"
    return None
