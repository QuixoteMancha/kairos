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
from datetime import date as _date
from typing import Any

import requests

from outer_apis.scrapers.brightdata_proxy import fetch_with_ip_block_fallback
from outer_apis.scrapers.tennis._request_throttle import tennisexplorer_throttle

_PLAYER_URL = "https://www.tennisexplorer.com/player/{slug}/"
_MATCH_DETAIL_URL = "https://www.tennisexplorer.com/match-detail/?id={match_id}"
_REQUEST_TIMEOUT_S = 20
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _fetch(slug: str, year: int | None = None) -> str | None:
    """Fail-closed: None ante cualquier fallo de red/status -- nunca
    devuelve HTML parcial/de error como si fuera válido. Throttle real
    (2026-08-04, pedido explícito -- "no overhittear los servers")
    ANTES de la request, thread-safe, compartido con cualquier otro
    caller de este módulo (backfills offline Y `prefetch_player_histories`
    en vivo, que usa un thread pool de hasta 16 workers).

    2026-08-11, pedido explícito del usuario ("los scrapers deben tener
    la característica de detectar cuando los sitios bloquean posiblemente
    por IP y en ese caso entrar al fallback de lanzar el request
    proxeado") -- request directa primero (como siempre), pero ahora vía
    `fetch_with_ip_block_fallback`: si TennisExplorer devuelve 403/429/503
    o la conexión falla, reintenta automático vía proxy BrightData (y esa
    capa, a su vez, whitelistea la IP del proxy si BrightData la rechaza)."""
    tennisexplorer_throttle.wait()
    url = _PLAYER_URL.format(slug=slug)
    if year is not None:
        url = f"{url}?annual={year}"
    resp = fetch_with_ip_block_fallback(url, headers={"User-Agent": _USER_AGENT}, timeout=_REQUEST_TIMEOUT_S)
    if resp is None or resp.status_code != 200:
        return None
    return resp.text


def _verify_player_page(html: str, expected_name: str) -> bool:
    """Confirma que la página devuelta es REALMENTE del jugador/a
    esperado -- nunca confía ciegamente en que el slug adivinado por
    `guess_player_slug` haya acertado.

    2026-08-06, bug real encontrado en vivo (verificado con caso real:
    "Casper Ruud" -> `guess_player_slug` adivinaba "ruud" -> esa URL es
    la página real de CHRISTIAN RUUD, su padre, también ex-jugador
    profesional -- una persona DISTINTA con el mismo apellido). La
    versión anterior de este chequeo toleraba "1 token de diferencia"
    contando la INTERSECCIÓN de tokens sin distinguir nombre de
    apellido -- eso pensado para "C. Ruud" vs "Casper Ruud" (mismo
    jugador, nombre abreviado) TAMBIÉN aceptaba, por error, a un
    jugador COMPLETAMENTE DISTINTO con el mismo apellido (el apellido
    solo ya bastaba para pasar la tolerancia). Alcance real medido:
    225/1,285 jugadores/as ya backfilleados comparten apellido con
    alguien más en el universo conocido -- riesgo real y amplio, no un
    caso aislado.

    Fix: el APELLIDO debe coincidir EXACTO (nunca se tolera diferencia
    ahí -- es la señal que definía "misma familia/apellido", no
    "misma persona"). El NOMBRE debe coincidir completo, O uno de los
    2 ser una inicial consistente con el otro (tolera "C. Ruud" vs
    "Casper Ruud", el caso real que motivó la tolerancia original).
    Fail-closed: sin apellido en `expected_name` (nombre de una sola
    palabra) o sin título parseable, siempre False -- nunca se asume."""
    m = re.search(r"<title>([^<]*) - Tennis Explorer</title>", html)
    if not m:
        return False
    expected_tokens = re.sub(r"[^a-zA-Z\- ]", "", expected_name).strip().split()
    page_tokens = re.sub(r"[^a-zA-Z\- ]", "", m.group(1)).strip().split()
    if len(expected_tokens) < 2 or len(page_tokens) < 2:
        return False
    expected_first, expected_last = expected_tokens[0].lower(), " ".join(expected_tokens[1:]).lower().replace("-", " ")
    page_first, page_last = page_tokens[0].lower(), " ".join(page_tokens[1:]).lower().replace("-", " ")
    if expected_last != page_last:
        return False
    if expected_first == page_first:
        return True
    if len(expected_first) == 1 or len(page_first) == 1:
        return expected_first[0] == page_first[0]
    # tolera apodo/nombre corto real (ej. "Alex" vs "Alexandra") -- SOLO
    # si es un prefijo real de al menos 3 letras, para no aceptar
    # coincidencias débiles de 1-2 letras que igual podrían ser 2
    # personas distintas.
    shorter, longer = sorted((expected_first, page_first), key=len)
    if len(shorter) >= 3 and longer.startswith(shorter):
        return True
    return False


def expected_opponent_abbreviation(full_name: str) -> str | None:
    """Reconstruye cómo TennisExplorer abreviaría a `full_name` cuando
    aparece como RIVAL en la página de otro jugador -- 2026-08-08,
    pedido explícito del usuario para deduplicar `TennisMatchHistory`
    (2 filas por partido real) sin fetch de red.

    Regla real verificada en vivo contra 1,471 jugadores/as reales de
    producción (97.5% match directo, el resto son casos que nunca
    fueron rival de nadie ya backfilleado -- no fallas de la regla):
    TODO menos el PRIMER token del nombre + espacio + inicial del
    primer token + "." -- NO es "solo el último apellido" (confirmado
    con apellidos compuestos reales: "Alan Fernando Rubio Fierros" ->
    rival aparece como "Rubio Fierros A.", "Adrian Menendez-Maceiras"
    -> "Menendez-Maceiras A.").

    LIMITACIÓN CONOCIDA: nombres de pila compuestos (ej. "Anna Karolina
    Schmiedlova", donde "Anna Karolina" es el nombre completo, no solo
    "Anna") rompen esta regla -- no hay forma de distinguir "nombre de
    pila compuesto" de "apellido compuesto" solo a partir del string
    plano. Por eso esto es SOLO un filtro de eficiencia para decidir
    qué re-fetchear, NUNCA la fuente de verdad de identidad -- eso lo
    resuelve `get_match_detail` contra la página real. Fail-closed:
    None si `full_name` es una sola palabra (no hay nombre+apellido que
    separar)."""
    parts = full_name.strip().split(" ")
    if len(parts) < 2:
        return None
    first, rest = parts[0], " ".join(parts[1:])
    return f"{rest} {first[0]}."


def guess_player_slug(player_name: str) -> str | None:
    """Ver LIMITACIÓN CONOCIDA en el docstring del módulo -- heurística
    verificada, no búsqueda real. Fail-closed: None si no hay match
    real confirmado.

    2026-08-12, bug real encontrado en vivo corriendo el backfill top-20
    ATP: "Felix Auger Aliassime" (rank 4) y "Alex De Minaur" (rank 7)
    fallaban -- el apellido real de ambos es COMPUESTO ("Auger
    Aliassime"/"De Minaur"), pero la heurística tomaba solo el ÚLTIMO
    token ("aliassime"/"minaur"), que ni siquiera existe como slug real
    en TennisExplorer (redirige al home, no a un jugador). Verificado en
    vivo: el slug real de un apellido compuesto es los ÚLTIMOS 2 tokens
    unidos con guion ("auger-aliassime", "de-minaur"). Se prueba primero
    el candidato de 1 token (comportamiento previo intacto, cubre la
    mayoría de apellidos simples), y si ese falla, el de 2 tokens como
    fallback -- nunca al revés, para no romper apellidos simples que ya
    funcionaban."""
    tokens = [t for t in player_name.strip().split() if t]
    if not tokens:
        return None

    candidates: list[str] = []
    last_token = re.sub(r"[^a-z\-]", "", tokens[-1].lower())
    if last_token:
        candidates.append(last_token)
    if len(tokens) >= 2:
        compound = re.sub(r"[^a-z\-]", "", "-".join(t.lower() for t in tokens[-2:]))
        if compound and compound not in candidates:
            candidates.append(compound)

    for candidate in candidates:
        html = _fetch(candidate)
        if html is not None and _verify_player_page(html, player_name):
            return candidate
    return None


def resolve_player_slug(player_name: str, candidate_slugs: list[str] = ()) -> str | None:
    """2026-08-06, pedido explícito del usuario: "corrige la data de
    matches por confusión de nombres" -- supera la LIMITACIÓN CONOCIDA
    de `guess_player_slug` (apellido homónimo entre hermanos/parientes
    en el circuito, ej. Naomi/Mari Osaka, Serena/Venus Williams,
    Alexander/Mischa Zverev, Stefanos/Apostolos Tsitsipas -- todos
    confirmados en vivo: el slug naive por apellido apunta al
    HERMANO/A, no a quien buscamos).

    Primero intenta la heurística naive (rápido, funciona para la
    mayoría sin colisión real). Si falla, prueba cada `candidate_slugs`
    (slugs REALES cosechados de los `href` de partidos ya fetcheados
    contra ese apellido, ver `TennisExplorerPlayerSlug` -- NO son
    adivinados, son URLs reales vistas en vivo) hasta encontrar uno que
    verifique. Fail-closed: `None` si ninguno confirma -- nunca se
    adivina cuál de los candidatos es, se exige verificación real
    contra el `<title>` de la página."""
    slug = guess_player_slug(player_name)
    if slug is not None:
        return slug
    for candidate in candidate_slugs:
        if not candidate:
            continue
        html = _fetch(candidate)
        if html is not None and _verify_player_page(html, player_name):
            return candidate
    return None


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
    # `.*?` (no `[^<]*`) para no cortar antes de esa subetiqueta. El
    # mismo <a> trae el `id` real de `/match-detail/?id=...` -- 2026-08-08,
    # pedido explícito del usuario (ver `get_match_detail` más abajo):
    # esa página resuelve identidad con certeza (nombre completo real +
    # ranking/edad/altura de AMBOS jugadores), y el id ya viene gratis
    # en este mismo HTML, sin request extra.
    r'<td class="tl">(?:<a[^>]*href="/match-detail/\?id=(?P<match_id>\d+)[^"]*"[^>]*>(?P<score_a>.*?)</a>|(?P<score_b>[^<]*))</td>\s*'
    r'<td class="course">(?P<odds_col1>[^<]*)</td>\s*'
    r'<td class="course">(?P<odds_col2>[^<]*)</td>',
    re.DOTALL,
)

_NOTU_RE = re.compile(r'class="notU"[^>]*><strong>([^<]*)</strong>')
_PLAIN_NAME_RE = re.compile(r'<a[^>]*>([^<]*)</a>')
_HREF_SLUG_RE = re.compile(r'href="/player/([a-z0-9\-]+)/?"')


def _extract_href_slug(block: str) -> str | None:
    """"/player/alcaraz-5ab70/" -> "alcaraz-5ab70" -- el slug REAL
    (con sufijo hash de desambiguación cuando aplica) tal cual lo usa
    TennisExplorer, extraído del link ya presente en cualquier fila de
    partido (sin request extra). Fail-closed: None si el bloque no
    tiene un link de jugador reconocible."""
    m = _HREF_SLUG_RE.search(block)
    return m.group(1) if m else None

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

        opponent_block = rm.group("player2_block") if self_is_first else rm.group("player1_block")
        opponent = _plain_name(opponent_block)
        if not opponent:
            continue
        # 2026-08-06, pedido explícito del usuario -- "corrige la data
        # de matches por confusión de nombres" -- el HREF real del
        # rival (ej. "/player/alcaraz-5ab70/") YA viene en el HTML que
        # se descarga (verificado en vivo), sin ningún request extra.
        # Cosecharlo acá permite desambiguar hermanos/homónimos
        # (Osaka/Williams/Zverev/Tsitsipas) sin adivinar por apellido
        # ni necesitar una búsqueda real -- ver
        # `resolve_player_slug`/`TennisExplorerPlayerSlug`.
        opponent_slug = _extract_href_slug(opponent_block)
        odds_self = rm.group("odds_col1") if self_is_first else rm.group("odds_col2")
        odds_opp = rm.group("odds_col2") if self_is_first else rm.group("odds_col1")
        score = _reorient_score_to_self(score, self_is_first)

        results.append({
            "date": rm.group("date").strip(),
            "tournament": current_tournament,
            "surface": rm.group("surface"),
            "opponent": opponent,
            "opponent_slug": opponent_slug,
            "round": rm.group("round").strip(),
            "round_full": rm.group("round_full"),
            "score": score,
            "odds_self": _odds_or_none(odds_self),
            "odds_opponent": _odds_or_none(odds_opp),
            "match_id": rm.group("match_id"),  # None si la fila no traía link a match-detail (raro, pero posible)
        })
    return results


def parse_player_retirements(html: str) -> list[dict[str, str | None]]:
    """Parse explicit in-match retirements from a player's Injuries table.

    TennisExplorer does not consistently put ``ret.`` in match-detail scores.
    The player page is the authoritative source: its ``playerInjuries`` table
    identifies the start date, tournament, reason and (when available) the
    partial score.  Walkovers are intentionally excluded because no player
    retired from a match that had started.
    """
    table_match = re.search(
        r'<table[^>]+id="playerInjuries"[^>]*>(.*?)</table>', html, re.DOTALL | re.IGNORECASE,
    )
    if table_match is None:
        return []

    retirements: list[dict[str, str | None]] = []
    seen: set[tuple[str, str]] = set()
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table_match.group(1), re.DOTALL | re.IGNORECASE):
        date_match = re.search(
            r'class="[^"]*\btime\b[^"]*"[^>]*>\s*(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4})',
            row,
        )
        tournament_match = re.search(
            r'class="[^"]*\btournament\b[^"]*"[^>]*>.*?<a[^>]*>([^<]+)</a>',
            row,
            re.DOTALL | re.IGNORECASE,
        )
        reason_match = re.search(
            r'<td[^>]+class="reason"(?P<attrs>[^>]*)>(?P<reason>.*?)</td>',
            row,
            re.DOTALL | re.IGNORECASE,
        )
        if date_match is None or tournament_match is None or reason_match is None:
            continue
        reason = re.sub(r"<[^>]+>", "", reason_match.group("reason")).strip()
        if not re.match(r"^retired\b", reason, re.IGNORECASE):
            continue
        raw_date = date_match.group(1)
        try:
            start_date = _date(*reversed([int(part) for part in raw_date.split(".")])).isoformat()
            raw_end_date = date_match.group(2)
            end_date = _date(*reversed([int(part) for part in raw_end_date.split(".")])).isoformat()
        except ValueError:
            continue
        tournament = tournament_match.group(1).strip()
        key = (start_date, tournament.casefold())
        if key in seen:
            continue
        seen.add(key)
        title_match = re.search(r'\btitle="([^"]*)"', reason_match.group("attrs"), re.IGNORECASE)
        score = re.sub(r"<[^>]+>", "", title_match.group(1)).strip() if title_match else None
        retirements.append({
            "start_date": start_date,
            "end_date": end_date,
            "tournament": tournament,
            "reason": reason,
            "score": score or None,
        })
    return retirements


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


def _to_iso_date(raw_date: str, reference_year: int) -> str | None:
    """'03.08.' (sin año -- TennisExplorer solo lo omite para filas del
    año en curso) -> ISO, usando `reference_year` (el año pedido en el
    fetch, correcto porque cada fetch es por año explícito). Fail-closed:
    None si no parsea (nunca fabrica una fecha)."""
    parts = [p for p in raw_date.strip().split(".") if p]
    if len(parts) < 2:
        return None
    day, month = parts[0], parts[1]
    try:
        return _date(reference_year, int(month), int(day)).isoformat()
    except ValueError:
        return None


def _derive_result_from_score(score: str) -> str:
    """W/L real contando sets ganados vs perdidos en el score REORIENTADO
    al dueño de la página (ya lo hace `_parse_year_html` vía
    `_reorient_score_to_self`) -- mismo criterio point-in-time que
    `scripts/wta_top200_historical_backfill.py::backfill_player`
    (extraído acá para reusar en `get_player_recent_matches`)."""
    sets_won = sets_lost = 0
    for set_str in (score or "").split(","):
        set_str = set_str.strip()
        if "-" not in set_str:
            continue
        parts = set_str.split("-")[:2]
        if len(parts) != 2:
            continue
        try:
            a, b = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        if a > b:
            sets_won += 1
        elif b > a:
            sets_lost += 1
    return "W" if sets_won > sets_lost else "L"


_NEXT_MATCH_RE = re.compile(
    # 2026-08-09, pedido explícito del usuario: la página de perfil de
    # TennisExplorer tiene una sección "Next match" (partido programado,
    # aún NO jugado) que ya trae un `match-detail/?id=...` REAL -- útil
    # para resolver el nombre completo de AMBOS jugadores de un partido
    # que el tick worker de Kalshi detecta ANTES de que se juegue (antes
    # de esto, `save_match_history` solo veía partidos YA jugados vía la
    # tabla de resultados, con el rival abreviado). Estructura real
    # verificada en vivo (perfil de Svitolina, ver memoria):
    # <span class="tab">Next match</span> ... <a href="TOURNAMENT_HREF">
    # TOURNAMENT</a> ... <td title="ROUND_FULL">ROUND</td> ...
    # <td class="time noWrp">START</td> ... <a href="/match-detail/
    # ?id=ID"...>MATCH_TEXT</a>. Fail-closed: `None` si la sección no
    # existe (jugador sin partido programado) o el HTML no matchea el
    # patrón esperado -- nunca se adivina un match_id.
    r'<span class="tab">Next match</span>.*?'
    r'<a href="([^"]*)"[^>]*>([^<]*)</a></td>\s*'
    r'<td title="([^"]*)"[^>]*>([^<]*)</td>\s*'
    r'<td class="time noWrp">([^<]*)</td>\s*'
    r'<th class="t-name"><a href="/match-detail/\?id=(\d+)[^"]*"[^>]*>([^<]*)</a></th>',
    re.DOTALL,
)


def get_next_match(player_name: str) -> dict[str, Any] | None:
    """Partido PROGRAMADO (aún no jugado) de la sección "Next match" del
    perfil del jugador -- devuelve el `match_id` real, listo para
    `get_match_detail` (ya tolera partidos sin score, ver
    `_MATCH_DETAIL_HEADER_RE`). NO resuelve identidad -- el `match_text`
    crudo (rivales abreviados, ej. "Alexandrova E. - Svitolina E.") es
    solo para que el caller pueda VALIDAR que este es el mismo partido
    que ya conoce por otra vía (ej. el mercado de Kalshi que disparó la
    consulta) antes de confiar en el `match_id`, per pedido explícito
    del usuario ("ese match tiene que ser validado").

    Fail-closed: `None` si no se resuelve el slug, el fetch falla, la
    página no verifica identidad, o no hay sección "Next match" (sin
    partido programado)."""
    slug = guess_player_slug(player_name)
    if slug is None:
        return None
    html = _fetch(slug)
    if html is None or not _verify_player_page(html, player_name):
        return None
    m = _NEXT_MATCH_RE.search(html)
    if m is None:
        return None
    tournament_href, tournament, round_full, round_abbrev, start_raw, match_id, match_text = m.groups()
    return {
        "match_id": match_id,
        "tournament": tournament.strip() or None,
        "tournament_href": tournament_href,
        "round": (round_full or round_abbrev).strip() or None,
        "start_raw": start_raw.strip() or None,
        "match_text": match_text.strip() or None,
        "tour": _tour_from_tournament_href(tournament_href),
    }


def get_player_recent_matches(player_name: str, n: int = 20) -> list[dict[str, Any]]:
    """Historial reciente de partidos de UN jugador vía TennisExplorer --
    2026-08-04, pedido explícito del usuario ("elimina cualquier
    referencia a flashscore" para la señal de forma reciente/H2H, ya que
    "podemos usar tennisexplorer perfectamente para sacar los scores, ya
    que veo que se actualiza rápido"). Reemplaza
    `flashscore_scraper.get_player_recent_matches` -- MISMO shape de
    retorno para no tener que tocar los callers (`match_history.py`).

    Returns: list de dicts (más reciente primero): {"fecha", "oponente",
    "resultado": "W"|"L", "score", "source": "tennisexplorer", "match_id"}.
    `match_id` (2026-08-09, pedido explícito del usuario: "save_match_history
    debe... sacar los nombres completos" en vez de guardar el rival
    abreviado) -- viene GRATIS del mismo HTML ya parseado (ver
    `_parse_year_html`), permite resolver identidad real de AMBOS lados
    vía `get_match_detail` en vez de persistir el nombre abreviado tal
    cual aparece en esta tabla; puede ser `None` en filas raras sin link
    a match-detail. Fail-closed: [] si no se encuentra el slug, el
    fetch falla, o no hay partidos parseables con fecha real. Trae año
    actual + año anterior (cubre jugadores a inicio de temporada sin
    necesitar más de 1 año extra -- `_MAX_DEPTH` de `match_history.py`
    es 40, un torneo activo ya cubre eso dentro del año en curso salvo
    casos raros de muy poca actividad)."""
    slug = guess_player_slug(player_name)
    if slug is None:
        return []

    current_year = _date.today().year
    out: list[dict[str, Any]] = []
    for year in (None, current_year - 1):
        html = _fetch(slug, year=year)
        if html is None or not _verify_player_page(html, player_name):
            continue
        resolved_year = current_year if year is None else year
        for m in _parse_year_html(html, player_name):
            iso_date = _to_iso_date(m["date"], resolved_year)
            if iso_date is None or not m.get("score"):
                continue
            out.append({
                "fecha": iso_date,
                "oponente": m["opponent"],
                "resultado": _derive_result_from_score(m["score"]),
                "score": m["score"],
                "source": "tennisexplorer",
                "match_id": m.get("match_id"),
            })

    out.sort(key=lambda r: r["fecha"], reverse=True)
    return out[:n]


def _fetch_match_detail(match_id: str) -> str | None:
    """Fail-closed: None ante cualquier fallo de red/status, mismo
    throttle compartido que `_fetch` (misma fuente, mismo servidor).
    2026-08-11: mismo fallback a proxy ante posible bloqueo por IP, ver
    docstring de `_fetch`."""
    tennisexplorer_throttle.wait()
    url = _MATCH_DETAIL_URL.format(match_id=match_id)
    resp = fetch_with_ip_block_fallback(url, headers={"User-Agent": _USER_AGENT}, timeout=_REQUEST_TIMEOUT_S)
    if resp is None or resp.status_code != 200:
        return None
    return resp.text


_MATCH_DETAIL_HEADER_RE = re.compile(
    # 2026-08-09, bug real encontrado en producción (pedido explícito
    # del usuario: soportar el "Next match" de la página del jugador,
    # que ya trae un `match-detail/?id=...` REAL antes de que el
    # partido se juegue): el bloque de score (`<td class="gScore">...
    # <br/><span>(...)` ) solo existe cuando el partido YA se jugó --
    # para un partido futuro esa celda es `<td class="gScore">&nbsp;</td>`
    # sin `<br/>` ni `<span>`, así que el regex original NUNCA matcheaba
    # y `get_match_detail` devolvía `None` para cualquier partido
    # todavía no jugado, pese a que la identidad de AMBOS jugadores
    # (nombre completo real, ranking, bio) YA está disponible en esa
    # misma página. El bloque de score ahora es OPCIONAL (grupo
    # alternativo `(?:...)?`) -- si no matchea, `score_a_sets`/
    # `sets_score` quedan `None`, tratado explícito más abajo.
    r'<th class="plName"[^>]*><a href="/player/([a-z0-9\-]+)/?"[^>]*>([^<]*)</a></th>\s*'
    r'<td class="gScore">(?:([^<]*)<br\s*/?>\s*<span>\(([^)]*)\)</span>)?[^<]*</td>\s*'
    r'<th class="plName"[^>]*><a href="/player/([a-z0-9\-]+)/?"[^>]*>([^<]*)</a></th>',
    re.DOTALL,
)
_MATCH_DETAIL_MATCH_INFO_RE = re.compile(
    r'<span class="upper">([^<]*)</span>,\s*[\d:]*,?\s*<a href="([^"]*)"[^>]*>([^<]*)</a>,\s*([^,<]*),\s*([^<]*)<'
)


def _te_native_to_nombre_apellido(name: str | None) -> str | None:
    """Convierte un nombre en formato NATIVO de TennisExplorer
    ("Apellido(s) Nombre", ej. "Svitolina Elina", confirmado en vivo
    repetidas veces en producción) a la convención "Nombre Apellido"
    (ej. "Elina Svitolina") -- 2026-08-10, pedido explícito del usuario:
    "para el backfill, primero ordenemos los nombres y apellidos, y que
    el scraper de tennisexplorer cuando entrega el nombre del player lo
    deje con esa nomenclatura". En TE, el ÚLTIMO token es siempre el
    nombre de pila; todo lo anterior es el apellido (soporta apellidos
    compuestos, ej. "Bouzas Maneiro Jessica" -> "Jessica Bouzas
    Maneiro"). Aplicado ACÁ (el punto de entrada real de todo nombre
    completo que resuelve el proyecto, `get_match_detail`) para que
    TODO consumidor (`save_match_detail`, el worker de backfill
    masivo, la migración a `Player`) reciba la convención ya
    normalizada, sin tener que reordenar después en cada lugar.

    Fail-closed: `None`/nombre de 1 solo token se devuelven tal cual
    (nunca se inventa un apellido que reordenar)."""
    if not name:
        return name
    tokens = name.strip().split(" ")
    if len(tokens) < 2:
        return name
    given = tokens[-1]
    surname = " ".join(tokens[:-1])
    return f"{given} {surname}"


def _tour_from_tournament_href(href: str | None) -> str | None:
    """"/santiago/2026/atp-men/" -> "ATP", "/masters-cup-wta/2025/wta-women/"
    -> "WTA" -- 2026-08-09, pedido explícito del usuario: "revisar si
    tennisexplorer muestra el torneo, tour y sexo de los players" --
    confirmado en vivo, el link del torneo en `match-detail` YA trae la
    categoría real ("atp-men"/"wta-women", también cubre "itf-men"/
    "itf-women" de nivel bajo por el mismo sufijo -men/-women) sin
    request adicional (viene en la MISMA página que ya se fetchea para
    identidad). Reemplaza la necesidad de `get_tour_by_sex` (que hacía
    un fetch aparte por jugador) para todo partido resuelto vía
    match-detail -- 1 sola fuente de verdad, más barata. Fail-closed:
    None si el href no matchea el patrón esperado (categorías raras
    tipo dobles mixtos/junior)."""
    if not href:
        return None
    slug = href.rstrip("/").rsplit("/", 1)[-1]
    if slug.endswith("-women"):
        return "WTA"
    if slug.endswith("-men"):
        return "ATP"
    return None


def _bio_row(html: str, label: str) -> tuple[str | None, str | None]:
    """Extrae (izquierda, derecha) de una fila `<th>{label}</th>` de la
    tabla de bio del match-detail -- ej. "Singles ranking", "Birthdate",
    "Height", "Weight", "Plays". Fail-closed: (None, None) si la fila
    no existe (algunos partidos viejos/de nivel bajo no tienen todos
    los campos, nunca se fabrica un valor)."""
    m = re.search(
        rf'<td class="tr">([^<]*)</td>\s*<th>{re.escape(label)}</th>\s*<td class="tl">([^<]*)</td>',
        html,
    )
    if not m:
        return None, None
    left, right = m.group(1).strip(), m.group(2).strip()
    return (left or None), (right or None)


def _parse_birthdate(raw: str | None) -> str | None:
    """"31. 8. 2001" -> "2001-08-31" (ISO). Fail-closed: None si no
    parsea con exactamente 3 partes numéricas."""
    if not raw or raw == "-":
        return None
    parts = [p.strip() for p in raw.split(".") if p.strip()]
    if len(parts) != 3:
        return None
    try:
        return _date(int(parts[2]), int(parts[1]), int(parts[0])).isoformat()
    except ValueError:
        return None


def _parse_int_prefix(raw: str | None) -> int | None:
    """"10." -> 10, "180 cm" -> 180, "59 kg" -> 59, "-" -> None.
    Fail-closed: None si no hay dígitos al inicio."""
    if not raw:
        return None
    m = re.match(r"(\d+)", raw)
    return int(m.group(1)) if m else None


def get_match_detail(match_id: str) -> dict[str, Any] | None:
    """Página `match-detail/?id=...` de TennisExplorer -- resuelve
    identidad de AMBOS jugadores de un partido con CERTEZA (nombre
    completo real + slug ya desambiguado, sin heurística de apellido),
    más ranking al momento del partido, fecha de nacimiento, altura,
    peso y mano dominante -- 2026-08-08, pedido explícito del usuario
    tras descubrir esta página investigando cómo evitar el matching
    fuzzy por apellido+fecha que causó los incidentes de misattribution
    de esta sesión (ver `_verify_player_page` para el detalle de esos
    incidentes).

    El `match_id` de cada fila de historial ya viene gratis en el HTML
    que se descarga de todos modos (ver `_MATCH_RE`), así que esto es 1
    request adicional por partido ÚNICO (no por fila -- cada partido
    real aparece 2 veces, una en la página de cada jugador, mismo id).

    Returns: dict con match_date/tournament/round/surface/score +
    player_a_*/player_b_* (name, slug, ranking, birthdate ISO, height_cm,
    weight_kg, plays) + winner_side ("a"|"b"|None) + retired (bool) +
    retired_side ("a"|"b"|None, el PERDEDOR cuando `retired=True`).
    Fail-closed: None si el fetch falla o el header de jugadores no
    parsea (nunca se fabrica un partido a medias) -- los campos de bio
    individuales SÍ pueden venir None por separado (fail-closed por
    campo, ver `_bio_row`).

    ``ret.`` in the score is retained as a compatible fallback. The
    reliable source is :func:`parse_player_retirements`, because real TE
    match-detail pages can omit that marker. Walkovers are not retirements.
    """
    html = _fetch_match_detail(match_id)
    if html is None:
        return None

    header = _MATCH_DETAIL_HEADER_RE.search(html)
    if header is None:
        return None
    a_slug, a_name, score_a_sets, sets_score, b_slug, b_name = header.groups()

    winner_side = None
    sets_m = re.match(r"\s*(\d+)\s*:\s*(\d+)\s*", score_a_sets) if score_a_sets else None
    if sets_m:
        a_sets, b_sets = int(sets_m.group(1)), int(sets_m.group(2))
        if a_sets > b_sets:
            winner_side = "a"
        elif b_sets > a_sets:
            winner_side = "b"

    # 2026-08-11, pedido explícito del usuario ("hay que agregar los
    # retiros de los players") -- convención estándar de tenis, el texto
    # de score puede traer "ret." al final cuando terminó por retiro
    # (ej. "7-5, 2-6, 6-4 ret."). El jugador retirado es
    # SIEMPRE el que perdió el partido (`winner_side` ya identifica al
    # ganador por sets, el lado contrario es quien se retiró) -- nunca
    # se adivina si el marcador no aparece en el texto (fail-closed).
    retired = bool(sets_score) and re.search(r"\bret\.?\b", sets_score, re.IGNORECASE) is not None
    retired_side = None
    if retired and winner_side is not None:
        retired_side = "b" if winner_side == "a" else "a"

    info = _MATCH_DETAIL_MATCH_INFO_RE.search(html)
    match_date = tournament = round_ = surface = tour = None
    if info:
        raw_date, tournament_href, tournament, round_, surface = info.groups()
        d = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", raw_date.strip())
        if d:
            match_date = f"{d.group(3)}-{d.group(2)}-{d.group(1)}"
        tournament = tournament.strip() or None
        round_ = round_.strip() or None
        surface = surface.strip() or None
        tour = _tour_from_tournament_href(tournament_href)

    a_rank_raw, b_rank_raw = _bio_row(html, "Singles ranking")
    a_birth_raw, b_birth_raw = _bio_row(html, "Birthdate")
    a_height_raw, b_height_raw = _bio_row(html, "Height")
    a_weight_raw, b_weight_raw = _bio_row(html, "Weight")
    a_plays_raw, b_plays_raw = _bio_row(html, "Plays")

    return {
        "match_id": match_id,
        "match_date": match_date,
        "tournament": tournament,
        "tour": tour,
        "round": round_,
        "surface": surface,
        "score": sets_score.strip() if sets_score else None,
        "winner_side": winner_side,
        "retired": retired,
        "retired_side": retired_side,
        "player_a_name": _te_native_to_nombre_apellido(a_name.strip()),
        "player_a_slug": a_slug,
        "player_a_ranking": _parse_int_prefix(a_rank_raw),
        "player_a_birthdate": _parse_birthdate(a_birth_raw),
        "player_a_height_cm": _parse_int_prefix(a_height_raw),
        "player_a_weight_kg": _parse_int_prefix(a_weight_raw),
        "player_a_plays": a_plays_raw if a_plays_raw and a_plays_raw != "-" else None,
        "player_b_name": _te_native_to_nombre_apellido(b_name.strip()),
        "player_b_slug": b_slug,
        "player_b_ranking": _parse_int_prefix(b_rank_raw),
        "player_b_birthdate": _parse_birthdate(b_birth_raw),
        "player_b_height_cm": _parse_int_prefix(b_height_raw),
        "player_b_weight_kg": _parse_int_prefix(b_weight_raw),
        "player_b_plays": b_plays_raw if b_plays_raw and b_plays_raw != "-" else None,
    }


_SEX_RE = re.compile(r"<div class=\"date\">Sex:\s*([a-zA-Z]+)</div>")


def get_tour_by_sex(player_name: str, candidate_slugs: list[str] = ()) -> str | None:
    """Resuelve "ATP"|"WTA" a partir del campo real "Sex: man"|"Sex:
    woman" de la página de perfil de TennisExplorer -- 2026-08-09,
    pedido explícito del usuario: "usar el scraper de tennisexplorer
    para obtener el sexo de los players" para resolver `tour` en
    `MatchHistory` donde ni otra fuente ni el roster WTA lo confirmaron.

    Verificado en vivo: "Sabalenka Aryna" -> "Sex: woman", "Djokovic
    Novak" -> "Sex: man". Fail-closed: None si no se resuelve el slug,
    el fetch falla, o el campo no aparece con el formato esperado --
    nunca se adivina el tour."""
    slug = resolve_player_slug(player_name, candidate_slugs=candidate_slugs)
    if slug is None:
        return None
    html = _fetch(slug)
    if html is None or not _verify_player_page(html, player_name):
        return None
    m = _SEX_RE.search(html)
    if not m:
        return None
    sex = m.group(1).strip().lower()
    if sex == "woman":
        return "WTA"
    if sex == "man":
        return "ATP"
    return None
