"""TennisExplorer.com — historial de partidos CON superficie por partido,
scraping crudo (búsqueda + fetch + parseo, sin lógica de negocio, mismo
patrón que flashscore_scraper.py).

CONTEXTO (2026-07-30, investigación en vivo): Flashscore (ver
flashscore_scraper.py) NO expone superficie por partido en ningún campo,
documentado ni indocumentado disponible. TennisExplorer SÍ la trae, por
partido, en la tabla "Played matches - {year} - singles" de la página de
cada jugador/a -- y a diferencia de Flashscore, esta página responde
DIRECTAMENTE sin necesitar el proxy residencial de BrightData (verificado
con requests reales durante esta sesión).

Formato de la tabla real (verificado en vivo, jugador Diana Shnaider,
2026-07-30):
  Cada torneo es una fila de encabezado (`tr.head.flags`), seguida de una
  fila por partido jugado en ese torneo:
    <td class="first time">02.07.</td>                    -- fecha DD.MM.
    <td class="s-color"><span title="grass" .../></td>     -- superficie real
    <td class="t-name">A - B</td>                          -- "Player1 - Player2"
    <td class="round" title="2. round">2R</td>
    <td class="tl"><a ...>6-4, 4-6, 6-2</a></td>            -- score, sets
      separados por coma, orientado a Player1-Player2 (mismo convenio que
      Flashscore: el primer número de cada set es de Player1).

Búsqueda: `/res/ajax/search.php?s={query}&t=p&all=1` (endpoint AJAX
público real usado por el autocompletado del sitio, encontrado inspeccionando
`main.js.php` -- no documentado oficialmente, pero estable y sin auth).

Fail-closed en cada paso: cualquier fallo de red/parseo devuelve []/None,
nunca fabrica un partido o una superficie."""

from __future__ import annotations

import re
import time
import unicodedata
from datetime import date
from typing import Any, Iterable, Iterator
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

_SEARCH_URL = "https://www.tennisexplorer.com/res/ajax/search.php"
_PLAYER_URL = "https://www.tennisexplorer.com/player/{slug}/"
_ALL_PLAYERS_URL = "https://www.tennisexplorer.com/list-players/"
_REQUEST_TIMEOUT_S = 15
_USER_AGENT = "Mozilla/5.0 (compatible; KairosResearchBot/1.0)"

# Rate limiting (2026-07-30, punto 2 pedido explícitamente por el usuario):
# TennisExplorer no tiene proxy/whitelisting como BrightData -- un delay
# fijo entre requests reales evita golpear el sitio de más en un scan con
# muchas jugadoras. No es agresivo (no hay evidencia de bloqueo), pero es
# la misma disciplina que REQUEST_DELAY_S en outer_apis/kalshi/portfolio.py.
_REQUEST_DELAY_S = 0.5
_last_request_ts: float = 0.0

_SURFACE_TITLE_MAP = {
    "hard": "Hard",
    "clay": "Clay",
    "grass": "Grass",
    "indoors": "Indoors",
    "carpet": "Indoors",  # TennisExplorer's rare "carpet" tag -- treated as indoor for our purposes
}

_ROW_RE = re.compile(
    r'<td class="first time">(\d{2})\.(\d{2})\.</td>\s*'
    r'<td class="s-color"><span title="([a-zA-Z]+)"[^>]*>.*?</span></td>\s*'
    r'<td class="t-name">(.*?)</td>\s*'
    r'<td class="round"[^>]*>.*?</td>\s*'
    r'<td class="tl"><a[^>]*>([^<]+)</a></td>',
    re.DOTALL,
)
_NAME_LINK_RE = re.compile(r"<a[^>]*>(?:<strong>)?([^<]+?)(?:</strong>)?</a>")
_SET_RE = re.compile(r"(\d+)(?:<sup>\d+</sup>)?-(\d+)(?:<sup>\d+</sup>)?")
_YEAR_TABLE_RE = re.compile(
    r'Played matches - (\d{4}) - singles.*?</ul>\s*<div id="matches-\d{4}-1-data"[^>]*>(.*?)</div>\s*<div id="matches-\d{4}-2-data"',
    re.DOTALL,
)


def _throttle() -> None:
    global _last_request_ts
    elapsed = time.monotonic() - _last_request_ts
    if elapsed < _REQUEST_DELAY_S:
        time.sleep(_REQUEST_DELAY_S - elapsed)
    _last_request_ts = time.monotonic()


def _norm(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9 ]", " ", ascii_str.lower()).strip()


def search_player(name: str, sex: str | None = None) -> str | None:
    """Devuelve el slug de URL (ej. "shnaider-51347") del mejor match de
    jugador/a por nombre, vía el endpoint AJAX real de búsqueda de
    TennisExplorer. Fail-closed: None si no hay match, la request falla,
    o la respuesta no es el JSON esperado (nunca adivina entre resultados
    ambiguos sin al menos un criterio de desambiguación por nombre)."""
    _throttle()
    try:
        resp = requests.get(
            _SEARCH_URL, params={"s": name, "t": "p", "all": "1"},
            headers={"User-Agent": _USER_AGENT}, timeout=_REQUEST_TIMEOUT_S,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None

    links = data.get("links") or []
    if not links:
        return None

    target_tokens = set(_norm(name).split())
    best_slug, best_score = None, -1.0
    for link in links:
        if link.get("type") != "p":
            continue
        if sex and link.get("sex") != sex:
            continue
        link_name = link.get("name", "")  # "Shnaider, Diana (RUS)"
        link_tokens = set(_norm(link_name.split("(")[0]).split(","))
        link_tokens = {t.strip() for chunk in link_tokens for t in chunk.split() if t.strip()}
        overlap = target_tokens & link_tokens
        score = len(overlap) / max(len(target_tokens), 1)
        if score > best_score:
            best_score, best_slug = score, link.get("url")

    return best_slug if best_score >= 0.5 else None


def _fetch_player_page(slug: str, year: int) -> str | None:
    """Fail-closed: None si la request falla o el status no es 200. Sin
    proxy -- confirmado alcanzable directo durante esta sesión."""
    _throttle()
    try:
        resp = requests.get(
            _PLAYER_URL.format(slug=slug), params={"annual": year},
            headers={"User-Agent": _USER_AGENT}, timeout=_REQUEST_TIMEOUT_S,
        )
        if resp.status_code != 200:
            return None
        return resp.text
    except requests.RequestException:
        return None


def _fetch_list_page(url: str) -> str | None:
    """Fetch a player-list page, applying the module-wide rate limit."""
    _throttle()
    try:
        resp = requests.get(
            url, headers={"User-Agent": _USER_AGENT}, timeout=_REQUEST_TIMEOUT_S,
        )
        if resp.status_code != 200:
            return None
        return resp.text
    except requests.RequestException:
        return None


def _country_links_from_html(html: str) -> list[str]:
    """Extract canonical country-list URLs and ignore unrelated links."""
    links: list[str] = []
    seen: set[str] = set()
    for anchor in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        url = urljoin(_ALL_PLAYERS_URL, anchor["href"])
        parsed = urlparse(url)
        country = parse_qs(parsed.query).get("country", [])
        if parsed.path != "/list-players/" or len(country) != 1:
            continue
        canonical = f"{_ALL_PLAYERS_URL}?country={country[0]}"
        if canonical not in seen:
            seen.add(canonical)
            links.append(canonical)
    return links


def get_all_player_country_links() -> list[str]:
    """Fetch ``/list-players/`` and return its nationality URLs.

    Fail-closed: an unavailable or malformed index produces an empty list.
    """
    html = _fetch_list_page(_ALL_PLAYERS_URL)
    return _country_links_from_html(html) if html else []


def _player_links_from_html(html: str) -> list[tuple[str, str]]:
    """Return ``(slug, displayed_name)`` pairs from a country page."""
    players: list[tuple[str, str]] = []
    seen: set[str] = set()
    for anchor in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        path = urlparse(urljoin(_ALL_PLAYERS_URL, anchor["href"])).path
        match = re.fullmatch(r"/player/([^/]+)/", path)
        if not match or match.group(1) in seen:
            continue
        name = anchor.get_text(" ", strip=True)
        if not name:
            continue
        slug = match.group(1)
        seen.add(slug)
        players.append((slug, name))
    return players


def _country_pagination_links(html: str, country_url: str) -> list[str]:
    """Extract pagination URLs that still belong to ``country_url``."""
    expected_country = parse_qs(urlparse(country_url).query).get("country", [None])[0]
    pages: list[str] = []
    for anchor in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        url = urljoin(country_url, anchor["href"])
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if (parsed.path == "/list-players/"
                and query.get("country", [None])[0] == expected_country
                and "page" in query):
            pages.append(url)
    return list(dict.fromkeys(pages))


def iter_all_player_pages(
    country_links: Iterable[str], year: int,
) -> Iterator[dict[str, str]]:
    """Fetch every player page found below the supplied country URLs.

    Country pagination is followed automatically. Players repeated across
    pages are fetched once. Failed list/player requests are skipped. Results
    are yielded incrementally as ``country_url``, ``player_url``, ``slug``,
    ``player_name`` and raw ``html`` so callers can reuse the existing player
    parsers without retaining the full site in memory.
    """
    seen_country_pages: set[str] = set()
    seen_players: set[str] = set()
    pending = list(country_links)

    while pending:
        country_url = urljoin(_ALL_PLAYERS_URL, pending.pop(0))
        if country_url in seen_country_pages:
            continue
        seen_country_pages.add(country_url)

        country_html = _fetch_list_page(country_url)
        if not country_html:
            continue
        pending.extend(
            url for url in _country_pagination_links(country_html, country_url)
            if url not in seen_country_pages
        )

        for slug, player_name in _player_links_from_html(country_html):
            if slug in seen_players:
                continue
            seen_players.add(slug)
            player_html = _fetch_player_page(slug, year)
            if not player_html:
                continue
            yield {
                "country_url": country_url,
                "player_url": _PLAYER_URL.format(slug=slug),
                "slug": slug,
                "player_name": player_name,
                "html": player_html,
            }


def _extract_names(t_name_html: str) -> tuple[str, str] | None:
    names = _NAME_LINK_RE.findall(t_name_html)
    names = [n.strip() for n in names if n.strip()]
    return (names[0], names[1]) if len(names) >= 2 else None


def _parse_sets(score_html: str) -> list[tuple[int, int]] | None:
    sets = _SET_RE.findall(score_html)
    if not sets:
        return None
    return [(int(a), int(b)) for a, b in sets]


def _parse_singles_table(html_fragment: str, year: int, subject_slug: str, subject_norm_tokens: set[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in _ROW_RE.finditer(html_fragment):
        day, month, surface_raw, t_name_html, score_html = m.groups()
        surface = _SURFACE_TITLE_MAP.get(surface_raw.lower())
        if surface is None:
            continue  # fail-closed: superficie no reconocida, se descarta la fila

        names = _extract_names(t_name_html)
        if names is None:
            continue
        name_1, name_2 = names

        sets = _parse_sets(score_html)
        if sets is None or len(sets) < 2:
            continue  # walkover/retiro sin secuencia completa -- se descarta

        # Determinar cuál de los 2 nombres es el/la sujeto (subject_norm_tokens).
        tokens_1 = set(_norm(name_1).split())
        tokens_2 = set(_norm(name_2).split())
        overlap_1 = len(subject_norm_tokens & tokens_1)
        overlap_2 = len(subject_norm_tokens & tokens_2)
        if overlap_1 == overlap_2:
            continue  # no se puede determinar de forma inequívoca cuál es el sujeto
        subject_is_first = overlap_1 > overlap_2
        opponent = name_2 if subject_is_first else name_1

        sets_won_1 = sum(1 for a, b in sets if a > b)
        sets_won_2 = sum(1 for a, b in sets if b > a)
        if sets_won_1 == sets_won_2:
            continue  # score inconsistente, defensivo
        subject_won = (sets_won_1 > sets_won_2) == subject_is_first

        try:
            match_date = date(year, int(month), int(day)).isoformat()
        except ValueError:
            continue  # fecha inválida (dato corrupto), se descarta

        out.append({
            "opponent": opponent,
            "match_date": match_date,
            "surface": surface,
            "result": "W" if subject_won else "L",
            "score": ", ".join(f"{a}-{b}" for a, b in sets),
        })

    return out


def get_player_matches(player_name: str, years: list[int], sex: str | None = None) -> list[dict[str, Any]]:
    """Historial real de partidos de singles CON superficie, para los
    `years` pedidos (típicamente año actual + anterior, para cubrir una
    ventana de ~6 meses sin importar en qué mes del año se corra esto).

    Returns:
        Lista de {"opponent", "match_date" ("YYYY-MM-DD"), "surface",
        "result" ("W"/"L"), "score"}, sin ordenar garantizado (el llamador
        ordena/filtra por fecha si lo necesita).

        Fail-closed: [] si el jugador no se encuentra, o [] acumulado si
        ninguna página de ningún año pudo fetchearse/parsearse -- nunca
        fabrica un partido."""
    slug = search_player(player_name, sex=sex)
    if slug is None:
        return []

    subject_norm_tokens = set(_norm(player_name).split())

    all_matches: list[dict[str, Any]] = []
    for year in years:
        html = _fetch_player_page(slug, year)
        if not html:
            continue
        m = _YEAR_TABLE_RE.search(html)
        if not m:
            continue
        table_year, fragment = int(m.group(1)), m.group(2)
        all_matches.extend(_parse_singles_table(fragment, table_year, slug, subject_norm_tokens))

    return all_matches
