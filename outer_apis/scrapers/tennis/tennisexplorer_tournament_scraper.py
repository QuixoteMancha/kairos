"""Página de TORNEO de TennisExplorer -- sede real (ciudad + país) y
nivel EXACTO derivado de los puntos de ranking.

2026-08-16, a raíz de la pregunta del usuario ("tennis explorer no trae
el nombre del torneo?") -- investigado en vivo y CONFIRMADO que sí, y
que trae bastante más que el nombre. Esto CORRIGE una afirmación previa
mía: yo había concluido que distinguir ATP 500 de ATP 250 era imposible
sin una fuente nueva (la API oficial de ATP está bloqueada, el repo de
Sackmann ya no existe) y lo había dejado como un genérico "ATP Tour".
Era incorrecto: el dato estaba disponible todo el tiempo, acá.

URL: `https://www.tennisexplorer.com/{slug}/{año}/{categoria}/`
(el mismo `tournament_href` que `get_match_detail` YA parsea hoy para
deducir el tour, ver `_tour_from_tournament_href`).

Lo que trae, verificado en vivo:
  - `<h1>`: "Santiago 2026 (Chile)", "Monte Carlo 2025 (Monaco)" ->
    CIUDAD + PAÍS. El país es clave para geocodificar bien: "santiago"
    a secas es ambiguo (Chile / Compostela / Cuba), "Santiago, Chile"
    no lo es.
  - Tabla "Ranking points" -> puntos del GANADOR, que son el nivel real
    del torneo. Verificado: Wimbledon 2000, Miami 1000, Indian Wells
    1000, Monte Carlo 1000, Hamburgo 500, Rotterdam 500, Adelaide WTA
    500, Delray Beach 250, Santiago 250.

LIMITACIÓN REAL (medida, no supuesta): la URL se RECONSTRUYE a partir
del nombre del torneo, y eso funciona para la mayoría del circuito
principal pero NO para challengers/ITF (usan slugs propios que no
derivan del nombre de la ciudad) ni para algunos torneos sueltos. Eso
no es un problema práctico: challenger/ITF ya obtienen su nivel del
sufijo del propio nombre, sin request (ver
`fill_tournament_metadata.derive_tournament_level`), así que este
scraper solo se usa para refinar los que quedaron como genéricos
"ATP Tour"/"WTA Tour". Fail-closed: si la página no existe o no
matchea, devuelve None y se conserva el genérico."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

import requests

from outer_apis.scrapers.tennis.tennisexplorer_scraper import _throttle

_BASE_URL = "https://www.tennisexplorer.com/{slug}/{year}/{category}/"
_REQUEST_TIMEOUT_S = 20
_USER_AGENT = "Mozilla/5.0 (compatible; KairosTennis/1.0)"

# Puntos del GANADOR -> nivel real. Valores verificados en vivo contra
# torneos reales (ver docstring del módulo).
_POINTS_TO_LEVEL_ATP = {
    2000: "Grand Slam", 1500: "Finals", 1000: "Masters 1000",
    500: "ATP 500", 250: "ATP 250",
}
_POINTS_TO_LEVEL_WTA = {
    2000: "Grand Slam", 1500: "Finals", 1000: "WTA 1000",
    500: "WTA 500", 250: "WTA 250", 125: "WTA 125",
}

_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S)
# "Santiago 2026 (Chile)" -> ciudad, país. El año va en el medio y se
# descarta (ya lo tenemos).
_H1_PARTS_RE = re.compile(r"^(.*?)\s+\d{4}\s*\((.+?)\)\s*$")


def _slugify(name: str) -> str:
    """"Monte Carlo" -> "monte-carlo", "Vitoria-Gasteiz" ->
    "vitoria-gasteiz". Sin acentos (TennisExplorer usa slugs ASCII)."""
    n = unicodedata.normalize("NFKD", name.strip().lower())
    n = n.encode("ascii", "ignore").decode("ascii")
    n = re.sub(r"[^a-z0-9]+", "-", n)
    return n.strip("-")


def _category_for_tour(tour: str | None) -> str | None:
    if tour == "ATP":
        return "atp-men"
    if tour == "WTA":
        return "wta-women"
    return None


def _parse_winner_points(html: str) -> int | None:
    """Puntos del ganador de la tabla "Ranking points". Fail-closed:
    None si la tabla no está o no matchea el patrón real."""
    idx = html.find("Ranking points")
    if idx < 0:
        return None
    # Se aplana el HTML a texto: la tabla real es
    # "... winner <premio> <moneda> <puntos> ...". La moneda varía
    # ($/€, verificado en vivo), por eso NO se asume ninguna.
    segment = re.sub(r"<[^>]+>", " ", html[idx: idx + 3000])
    segment = re.sub(r"\s+", " ", segment)
    match = re.search(r"winner\s+[\d,]+\s*[^\d\s]+\s+([\d,]+)", segment)
    if match is None:
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return None


def level_from_winner_points(points: int | None, tour: str | None) -> str | None:
    """Nivel real a partir de los puntos del ganador. Fail-closed: None
    si los puntos no corresponden a un nivel conocido -- nunca se
    aproxima al nivel "más cercano"."""
    if points is None:
        return None
    table = _POINTS_TO_LEVEL_WTA if tour == "WTA" else _POINTS_TO_LEVEL_ATP
    return table.get(points)


def get_tournament_info(
    tournament_name: str, year: int, tour: str | None,
) -> dict[str, Any] | None:
    """Sede y nivel reales de un torneo, o None (fail-closed).

    Returns:
        {"city", "country", "winner_points", "level"} -- `level` puede
        ser None aunque la página exista (puntos no reconocidos), en
        cuyo caso el caller debe conservar lo que ya tenía."""
    category = _category_for_tour(tour)
    if not tournament_name or not tournament_name.strip() or category is None:
        return None

    slug = _slugify(tournament_name)
    if not slug:
        return None

    _throttle()
    try:
        resp = requests.get(
            _BASE_URL.format(slug=slug, year=year, category=category),
            headers={"User-Agent": _USER_AGENT},
            timeout=_REQUEST_TIMEOUT_S,
        )
        if resp.status_code != 200:
            return None
        html = resp.text
    except requests.RequestException:
        return None

    h1_match = _H1_RE.search(html)
    if h1_match is None:
        return None
    h1 = re.sub(r"<[^>]+>", "", h1_match.group(1)).strip()
    parts = _H1_PARTS_RE.match(h1)
    if parts is None:
        return None

    city = parts.group(1).strip()
    country = parts.group(2).strip()
    points = _parse_winner_points(html)

    return {
        "city": city or None,
        "country": country or None,
        "winner_points": points,
        "level": level_from_winner_points(points, tour),
    }
