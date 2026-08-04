"""TennisExplorer.com -- historial COMPLETO de partidos por jugador/a,
por año, sin proxy (2026-08-03, pedido explícito del usuario: "mira que
tennisexplorer sí tiene data de WTA, puedes investigar si se puede
hacer fetch de jugadoras?" -> "sí mejora el scraper... con estas
features").

FUENTE: `https://www.tennisexplorer.com/player/{slug}/` (año actual) y
`?annual={YYYY}` para años anteriores -- verificado en vivo (200 OK,
sin proxy, HTML estático completo por año -- NO es AJAX/paginado como
Flashscore, cada año es una URL normal). Reemplaza el copy-paste manual
que motivó esta investigación (gap real encontrado en
`flashscore_scraper.py::get_player_recent_matches`: se perdió un
partido real de Eala vs Sasnovich, Abu Dhabi 2026-02-04, confirmado con
WebSearch).

HALLAZGO ADICIONAL IMPORTANTE: la tabla trae columnas "H"/"A" con
CUOTAS DE APUESTA REALES por partido (decimal odds, ej. 2.36 / 1.58) --
"H" = cuota del propio jugador/a (columna izquierda, siempre el dueño
de la página, marcado con `class="notU"` + `<strong>`), "A" = cuota
del rival. Es la PRIMERA fuente de precio de mercado real histórico
encontrada en toda la investigación de esta sesión (TML-Database no
tiene ningún precio). Probabilidad implícita cruda = 1/cuota (SIN
remover el margen/vig de la casa -- eso queda para quien consuma este
dato, no se hace acá).

LIMITACIÓN CONOCIDA: no existe un endpoint de búsqueda por nombre
confirmado (investigado, la URL `/search/?q=...` no devuelve resultados
reales) -- `guess_player_slug` es una heurística (apellido en minúsculas,
convención observada) verificada contra el `<title>` real de la página
resultante, fail-closed a None si no coincide o hay colisión de
apellido (TennisExplorer desambigua con sufijos hash tipo "osaka-fa603"
para jugadores homónimos, que esta heurística no puede adivinar). Si
falla, pasar el slug real explícito (visible en cualquier link
`/player/slug/` ya conocido, ej. de otro partido)."""

from __future__ import annotations

import re
from typing import Any

import requests

_PLAYER_URL = "https://www.tennisexplorer.com/player/{slug}/"
_REQUEST_TIMEOUT_S = 20
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _fetch(slug: str, year: int | None = None) -> str | None:
    """Fail-closed: None ante cualquier fallo de red/status -- nunca
    devuelve HTML parcial/de error como si fuera válido."""
    url = _PLAYER_URL.format(slug=slug)
    if year is not None:
        url = f"{url}?annual={year}"
    try:
        resp = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=_REQUEST_TIMEOUT_S)
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    return resp.text


def _name_tokens(name: str) -> set[str]:
    return set(re.sub(r"[^a-zA-Z ]", "", name).lower().split())


def _verify_player_page(html: str, expected_name: str) -> bool:
    """Confirma que la página devuelta es REALMENTE del jugador/a
    esperado -- nunca confía ciegamente en que el slug adivinado por
    `guess_player_slug` haya acertado."""
    m = re.search(r"<title>([^<]*) - Tennis Explorer</title>", html)
    if not m:
        return False
    expected = _name_tokens(expected_name)
    page = _name_tokens(m.group(1))
    if not expected:
        return False
    overlap = expected & page
    return len(overlap) >= max(1, len(expected) - 1)  # tolera 1 token de diferencia (ej. inicial vs nombre completo)


def guess_player_slug(player_name: str) -> str | None:
    """Ver LIMITACIÓN CONOCIDA en el docstring del módulo -- heurística
    verificada, no búsqueda real. Fail-closed: None si no hay match
    real confirmado."""
    last_name = player_name.strip().split()[-1].lower() if player_name.strip() else ""
    last_name = re.sub(r"[^a-z\-]", "", last_name)
    if not last_name:
        return None
    html = _fetch(last_name)
    if html is None or not _verify_player_page(html, player_name):
        return None
    return last_name


_MATCH_RE = re.compile(
    r'<td class="first time">(?P<date>[^<]*)</td>\s*'
    r'<td class="s-color"><span title="(?P<surface>[^"]*)"[^>]*>&nbsp;</span></td>\s*'
    # 2026-08-03, bug real encontrado: el dueño de la página (marcado con
    # class="notU"+<strong>) NO siempre aparece primero en la fila -- a
    # veces el rival va primero (ej. "Paolini J. - Eala A."). Se capturan
    # los 2 jugadores CON su bloque completo (para detectar cuál trae
    # notU) en vez de asumir una posición fija.
    r'<td class="t-name">(?P<player1_block><a[^>]*>.*?</a>)\s*-\s*(?P<player2_block><a[^>]*>.*?</a>)</td>\s*'
    # `title="..."` (nombre completo de la ronda) es OPCIONAL -- filas
    # reales sin ronda asignada vienen como `<td class="round">&nbsp;</td>`.
    r'<td class="round"(?:[^>]*title="(?P<round_full>[^"]*)")?[^>]*>(?P<round>[^<]*)</td>\s*'
    # score puede traer tiebreak en <sup>N</sup> dentro del propio <a> --
    # `.*?` (no `[^<]*`) para no cortar antes de esa subetiqueta.
    r'<td class="tl">(?:<a[^>]*>(?P<score_a>.*?)</a>|(?P<score_b>[^<]*))</td>\s*'
    r'<td class="course">(?P<odds_col1>[^<]*)</td>\s*'
    r'<td class="course">(?P<odds_col2>[^<]*)</td>',
    re.DOTALL,
)

_NOTU_RE = re.compile(r'class="notU"[^>]*><strong>([^<]*)</strong>')
_PLAIN_NAME_RE = re.compile(r'<a[^>]*>([^<]*)</a>')

def _clean_score(raw: str) -> str:
    """Quita los tiebreaks en `<sup>` (ej. '7-6<sup>1</sup>' -> '7-6') --
    no se necesita el punto exacto del tiebreak para el uso actual
    (determinar quién ganó cada set), y así queda un string 'N-M, N-M'
    consistente y fácil de volver a parsear."""
    return re.sub(r"<sup>\d+</sup>", "", raw).strip()


def _reorient_score_to_self(score: str, self_is_first: bool) -> str:
    """El score real viene en el MISMO orden en que TennisExplorer lista
    a los 2 jugadores en la fila (confirmado en vivo: 'Paolini - Eala',
    '6-4, 4-6, 6-3', Paolini ganó 2-1 -- primer número de cada set es
    SIEMPRE el primer jugador listado, no el ganador del set ni del
    partido). Si el dueño de la página (self) viene SEGUNDO en la fila,
    hay que invertir cada set para que el string quede consistente
    (self primero), igual convención que `flashscore_scraper.py::
    get_player_recent_matches`."""
    if self_is_first:
        return score
    flipped_sets = []
    for set_str in score.split(","):
        set_str = set_str.strip()
        m = re.match(r"^(\d+)-(\d+)$", set_str)
        if not m:
            flipped_sets.append(set_str)
            continue
        flipped_sets.append(f"{m.group(2)}-{m.group(1)}")
    return ", ".join(flipped_sets)


def _parse_year_html(html: str, self_name_hint: str) -> list[dict[str, Any]]:
    """Extrae los partidos de individuales del año pedido -- SOLO la
    sección 'singles' de la primera pestaña `matches-{year}-1-data`
    (no dobles/mixtos), fail-closed por fila (una fila sin todos los
    campos reales no se agrega, nunca se fabrica)."""
    tab_match = re.search(r'id="matches-\d{4}-1-data"[^>]*>(.*?)(?=id="matches-\d{4}-2-data"|\Z)', html, re.DOTALL)
    if tab_match is None:
        return []
    block = tab_match.group(1)

    # recorrer en orden real: encabezados de torneo intercalados con filas de partido
    current_tournament = None
    results: list[dict[str, Any]] = []
    pos = 0
    # 2026-08-04, bug real encontrado investigando por qué 48,127 filas
    # de source="tennisexplorer" quedaron con tournament=None: el regex
    # original hardcodeaba `class="type-women2"` -- pero la fuente
    # categoriza el TORNEO (no la jugadora) con esa clase, y un torneo
    # cualquiera (ej. "Czech league", un evento de equipos) puede venir
    # marcado `type-men2` incluso en la página de una jugadora WTA
    # (inconsistencia real de la fuente, no nuestra). Como
    # `current_tournament` se actualiza secuencialmente, UN SOLO header
    # que no matcheaba dejaba sin torneo a TODOS los partidos
    # siguientes hasta el próximo header que sí matcheara -- de ahí el
    # volumen tan grande de filas afectadas. Fix: aceptar cualquier
    # variante de esa clase (`type-\w+`), no solo `type-women2`.
    combined_re = re.compile(
        r'(?P<header><tr class="head flags">\s*<td class="t-name"[^>]*><a href="[^"]*">'
        r'<span class="fl[^"]*">&nbsp;</span><span class="type-\w+">&nbsp;</span>[^<]*</a>)'
        r'|'
        r'(?P<row><tr class="(?:one|two)">.*?</tr>\s*)',
        re.DOTALL,
    )
    for m in combined_re.finditer(block):
        if m.group("header"):
            tm = re.search(r'>([^<]*)</a>$', m.group("header"))
            if tm:
                current_tournament = tm.group(1).strip()
            continue
        row_html = m.group("row")
        if row_html is None or "t-name" not in row_html:
            continue
        rm = _MATCH_RE.search(row_html)
        if rm is None:
            continue
        raw_score = rm.group("score_a") if rm.group("score_a") is not None else (rm.group("score_b") or "")
        score = _clean_score(raw_score)
        if not score or not re.search(r"\d-\d", score):
            continue  # sin score real parseable (ej. "Next opponent is not known yet")

        def _odds_or_none(raw: str) -> float | None:
            try:
                return float(raw.strip())
            except ValueError:
                return None

        # ¿cuál de los 2 bloques es el dueño de la página (notU)? Puede
        # venir primero o segundo en la fila -- nunca asumir posición fija.
        notu1 = _NOTU_RE.search(rm.group("player1_block"))
        notu2 = _NOTU_RE.search(rm.group("player2_block"))
        if notu1 is not None:
            self_is_first = True
        elif notu2 is not None:
            self_is_first = False
        else:
            continue  # ninguno de los 2 trae notU -- fila inesperada, no fabricar

        def _plain_name(block: str) -> str | None:
            pm = _PLAIN_NAME_RE.search(block)
            return pm.group(1).strip() if pm else None

        opponent = _plain_name(rm.group("player2_block") if self_is_first else rm.group("player1_block"))
        if not opponent:
            continue
        odds_self = rm.group("odds_col1") if self_is_first else rm.group("odds_col2")
        odds_opp = rm.group("odds_col2") if self_is_first else rm.group("odds_col1")
        score = _reorient_score_to_self(score, self_is_first)

        results.append({
            "date": rm.group("date").strip(),
            "tournament": current_tournament,
            "surface": rm.group("surface"),
            "opponent": opponent,
            "round": rm.group("round").strip(),
            "round_full": rm.group("round_full"),
            "score": score,
            "odds_self": _odds_or_none(odds_self),
            "odds_opponent": _odds_or_none(odds_opp),
        })
    return results


def get_player_match_history(
    slug: str, player_display_name: str, years: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Historial real de partidos de individuales de `slug` (ver
    `guess_player_slug` si no se conoce de antemano), para cada año en
    `years` (default: solo el año actual del sitio, sin parámetro
    `annual`).

    Returns: lista de dicts {date, tournament, surface, opponent,
    round, round_full, score, odds_self, odds_opponent} -- más reciente
    primero POR AÑO (el orden entre años no está garantizado si se
    piden varios, ordenar por fecha real si se necesita cronológico).
    `odds_self`/`odds_opponent`: cuota decimal real de la casa que
    trackea TennisExplorer, `None` si no había cuota registrada para
    ese partido (partidos de niveles menores a veces no tienen). Fail-
    closed: [] si el fetch falla o no hay partidos parseables.

    `player_display_name` se usa SOLO para verificar (vía `<title>`)
    que la página fetcheada es realmente de esa persona -- nunca se
    confía en el slug a ciegas."""
    all_results: list[dict[str, Any]] = []
    year_list = years if years is not None else [None]
    for year in year_list:
        html = _fetch(slug, year=year)
        if html is None or not _verify_player_page(html, player_display_name):
            continue
        all_results.extend(_parse_year_html(html, player_display_name))
    return all_results
