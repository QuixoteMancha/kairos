"""
Flashscore scraper — historial de partidos reales de tenis, rescatado de
la arquitectura pre-"extreme makeover" (`src/scrapers/flashscore_scraper.py`,
borrado en el commit `02d7f35`) y adaptado a `outer_apis/scrapers/brightdata_proxy.py`.

Cobertura: incluye jugadores fuera de los top-N que trae el scraper de
rankings oficiales de ATP/WTA (torneos challenger/ITF de bajo nivel).

Estrategia:
  1. Búsqueda de jugador vía API pública de Flashscore (sin proxy).
  2. Página de resultados del jugador vía proxy residencial de BrightData
     (Flashscore bloquea scraping directo de esta página sin proxy).
  3. Parseo de datos embebidos en formato pipe-delimitado propio de
     Flashscore.

Formato interno de Flashscore (sin cambios, documentado en el código
rescatado):
  Registros separados por "~AA÷", campos por "¬", clave÷valor.
  AB÷3 = partido terminado. PX÷/PY÷ = participant_id P1/P2.
  FH÷/FK÷ = nombres completos P1/P2. AS÷1/AS÷2 = ganador P1/P2.
  AG÷/AH÷ = sets P1/P2. BA÷BB÷ = juegos set1 P1/P2, BC÷BD÷ = set2, etc.

Este módulo SOLO hace scraping crudo (búsqueda + fetch + parseo). La
superficie pública de llamada vive en `outer_apis/tennis_match_history.py`.
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, UTC
from typing import Any

from outer_apis.scrapers import brightdata_proxy
from outer_apis.scrapers.tennis._request_throttle import flashscore_throttle

_SEARCH_URL = "https://s.flashscore.com/search/?q={name}&l=1&s=2&f=1%3B1&pid=2&sid=2"
_RESULTS_URL = "https://www.flashscore.com/player/{slug}/{pid}/results/"
# 2026-08-10, pedido explícito del usuario: "aplicar la verificación de
# nombre tanto cuando el scraper usa proxy como cuando no" -- la página
# BASE de perfil (sin el sufijo `/results/`) SÍ es accesible sin proxy
# (verificado en vivo: Flashscore bloquea `/results/`, no la ficha base),
# a diferencia de `_RESULTS_URL` (esa sí requiere BrightData). Se usa
# ÚNICAMENTE para confirmar identidad real (`heading__name`), nunca
# para traer partidos/stats -- eso sigue exclusivo de `_RESULTS_URL`.
_PROFILE_URL = "https://www.flashscore.com/player/{slug}/{pid}/"

# 2026-08-04, pedido explícito del usuario: "el proxy ya no se usará
# hasta nuevo aviso para fetchs" -- se pausó el fetch por env var en vez
# de borrar la estructura.
#
# 2026-08-16: la premisa de arriba resultó FALSA y el gate ya NO gatea
# nada. Se creía que `find_match_id`/`get_match_statistics` requerían
# proxy sí o sí ("Flashscore bloquea esas 2 páginas sin proxy"); medido
# en vivo, ambas responden HTTP 200 con datos reales SIN proxy. El gate
# se fue removiendo por partes a pedido explícito del usuario:
#   - `_fetch_results_page` (2026-08-12)
#   - `get_match_statistics` (2026-08-16) -- tenía `tennis_match_stats`
#     en 0 filas en producción pese a estar el pipeline entero wireado,
#     porque la función cortaba en su primera línea.
# Todas usan ahora `fetch_with_ip_block_fallback`: directo primero,
# proxy SOLO ante señal real de bloqueo (403/429/503 o error de red).
#
# Tras remover el gate de `get_match_statistics` NO queda ninguna función
# gateada por esta constante (verificado por grep 2026-08-16: cero
# ocurrencias de `if not PROXY_FETCH_ENABLED` en el módulo). Se CONSERVA
# igual, no como código muerto sino como el interruptor documentado del
# módulo: `fetch_with_ip_block_fallback` decide solo cuándo usar proxy,
# y esta env var queda como referencia de esa decisión histórica.
# Eliminarla es una limpieza aparte, no parte de este cambio.
PROXY_FETCH_ENABLED = os.getenv("FLASHSCORE_PROXY_FETCH_ENABLED", "false").strip().lower() == "true"

_SET_FIELDS = [("BA", "BB"), ("BC", "BD"), ("BE", "BF"), ("BG", "BH"), ("BI", "BJ")]

_player_id_cache: dict[str, tuple[str, str] | None] = {}


# ── Búsqueda de jugador (sin proxy) ────────────────────────────────────────

def _verify_flashscore_player(pid: str, slug: str, expected_name: str) -> bool:
    """Confirma que `(pid, slug)` es REALMENTE `expected_name` -- 2026-08-10,
    pedido explícito del usuario ("aplicar la verificación de nombre
    tanto cuando el scraper usa proxy como cuando no") tras encontrar en
    vivo que `search_player` confiaba ciegamente en el PRIMER resultado
    de la búsqueda (Flashscore devolvió 7 candidatos "Elina ___" reales
    para "Elina Svitolina": monfils-elina, avanesyan-elina, nepliy-elina,
    elina-zakharova, popescu-elina, kamyabsharifi-elina,
    lopez-pappalardo-elina-maria -- tomaba el primero sin chequear,
    acertó por casualidad esa vez).

    Fetchea `_PROFILE_URL` (la ficha BASE, sin `/results/` -- esa sí
    requiere proxy BrightData; la base NO, confirmado en vivo) y compara
    el nombre real mostrado (`heading__name`) contra `expected_name` por
    solapamiento de tokens (`_significant_tokens`, mismo criterio que
    `_name_score`) -- funciona SIN proxy (fetch directo) y también
    cuando el proxy está activo (mismo mecanismo, no depende de
    `PROXY_FETCH_ENABLED`). Fail-closed: `False` ante cualquier fallo de
    red/parseo, o si el nombre mostrado no comparte ningún token
    significativo real con `expected_name`.

    2026-08-11, pedido explícito del usuario ("los scrapers deben tener
    la característica de detectar cuando los sitios bloquean posiblemente
    por IP") -- request directa primero como siempre, pero vía
    `fetch_with_ip_block_fallback`: si Flashscore devuelve 403/429/503 o
    la conexión falla, reintenta automático por proxy BrightData."""
    try:
        flashscore_throttle.wait()
        url = _PROFILE_URL.format(slug=slug, pid=pid)
        resp = brightdata_proxy.fetch_with_ip_block_fallback(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://www.flashscore.com/",
            },
            timeout=10,
        )
        if resp is None or resp.status_code != 200:
            return False
        m = re.search(r'heading__name[^>]*>([^<]+)<', resp.text)
        if not m:
            return False
        page_name = m.group(1).strip()
        # 2026-08-10, bug real encontrado en vivo: comparar por
        # solapamiento de CUALQUIER token es insuficiente -- "Elina
        # Svitolina" vs "Elina Zakharova" comparten el nombre de pila
        # ("Elina") y esto los confundía como la misma persona. Exige
        # coincidencia del APELLIDO (heurística ya probada en
        # `database/historical/tennis/writer.py::_names_confirm_identity`:
        # el token MÁS LARGO de cada nombre, agnóstico al orden).
        expected_tokens = _significant_tokens(expected_name)
        page_tokens = _significant_tokens(page_name)
        if not expected_tokens or not page_tokens:
            return False
        expected_surname = max(expected_tokens, key=len)
        page_surname = max(page_tokens, key=len)
        return expected_surname == page_surname
    except Exception:
        return False


def search_player(name: str) -> tuple[str, str] | None:
    """Busca un jugador de tenis por nombre en la API pública de
    Flashscore (endpoint público JSON/JSONP, sin proxy) -- 2026-08-10,
    verifica identidad real contra CADA candidato en orden (nunca
    confía en la posición del primer resultado, ver
    `_verify_flashscore_player`) antes de devolverlo.

    Returns:
        (participant_id, url_slug) o None si no se encuentra / falla /
        ningún candidato verifica.
    """
    cache_key = name.lower().strip()
    if cache_key in _player_id_cache:
        return _player_id_cache[cache_key]

    result = None
    try:
        # 2026-08-04, pedido explícito -- throttle real ANTES de la
        # request (no antes del cache-hit de arriba, para no penalizar
        # nombres ya conocidos), "no overhittear los servers".
        # 2026-08-11: fallback a proxy ante posible bloqueo por IP, ver
        # docstring de `_verify_flashscore_player`.
        flashscore_throttle.wait()
        url = _SEARCH_URL.format(name=name.replace(" ", "%20"))
        resp = brightdata_proxy.fetch_with_ip_block_fallback(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://www.flashscore.com/",
            },
            timeout=10,
        )
        m = re.search(r"\((.+)\)", resp.text, re.DOTALL) if resp is not None else None
        if m:
            import json

            data = json.loads(m.group(1))
            for item in data.get("results", []):
                if item.get("sport_id") == 2 and item.get("type") == "participants":
                    candidate = (item["id"], item.get("url", ""))
                    if _verify_flashscore_player(candidate[0], candidate[1], name):
                        result = candidate
                        break
    except Exception:
        result = None

    _player_id_cache[cache_key] = result
    return result


# ── Fetch de resultados (directo, con fallback a proxy BrightData) ─────────

# 2026-08-12, pedido explícito del usuario ("Opción B para descubrir el
# endpoint y qué parámetros necesita") -- el HTML estático `/results/`
# (`_RESULTS_URL`, dejado de lado abajo) solo embebe una ventana ACOTADA
# (~144 partidos/jugador, confirmado real, no un límite de nuestro
# parser) porque la paginación real de Flashscore ocurre 100% client-side
# (React SPA, botón "Show more matches"). Encontrado capturando tráfico
# de red real con Playwright (headless Chromium) tras que el análisis
# estático (grep del HTML por JSON/season-id embebido, sondeo a ciegas de
# variantes del patrón de feed ya conocido, búsqueda pública de
# documentación) no lo encontrara -- ver memoria de la sesión.
#
# Patrón real: `pr_2_167_{pid}_{page}_-4_en_1_s`. `page` es un contador
# simple (0 = más reciente, incrementa retrocediendo en el tiempo, SIN
# huecos ni superposición real de temporadas -- confirmado). TODO lo
# demás es constante universal, verificado idéntico contra 3 jugadores
# reales de tours/géneros distintos (Djokovic ATP, Alcaraz ATP, Świątek
# WTA): `2`=sport_id tenis, `167`=id fijo interno de Flashscore (también
# aparece embebido dentro de cada record individual, `CB÷167`), `-4`,
# `en`=idioma, `1`, `s` -- ninguno depende del jugador. Cada página trae
# ~40 registros reales, mismo formato pipe-delimited que el HTML viejo
# (`~ZA÷` headers de torneo + `~AA÷` records), sin gaps cronológicos.
# Verificado end-to-end con Djokovic: 37 páginas, ~1,490 partidos reales,
# termina en 2004 (su debut junior) -- cobertura de carrera COMPLETA,
# no solo los ~144 más recientes de antes.
_PLAYER_RESULTS_FEED_URL = "https://global.flashscore.ninja/2/x/feed/pr_2_167_{pid}_{page}_-4_en_1_s"
_MAX_RESULTS_PAGES = 45  # cap de seguridad -- Djokovic (carrera larguísima) usó 37


def _fetch_results_page(
    pid: str, slug: str | None = None, target_date: str | None = None,
    max_pages: int = _MAX_RESULTS_PAGES,
) -> str | None:
    """Obtiene el historial de resultados del jugador, paginando el feed
    real `_PLAYER_RESULTS_FEED_URL` (ver comentario arriba) hasta cubrir
    lo necesario. `slug` ya no se usa (el feed real solo necesita
    `pid`) -- se mantiene el parámetro por compatibilidad con callers/
    tests existentes.

    `target_date` (ISO string, opcional): permite CORTE TEMPRANO -- deja
    de pedir páginas nuevas en cuanto la página más vieja ya fetcheada
    cubre una fecha igual o anterior a `target_date` (no hace falta
    traer la carrera completa para resolver un partido de hace 2 años).
    Sin `target_date`, se detiene solo cuando Flashscore devuelve una
    página vacía (fin real de la carrera) o al llegar a `max_pages`.

    `max_pages`: cap explícito de páginas -- default `_MAX_RESULTS_PAGES`
    (carrera completa), pero callers que solo quieren partidos RECIENTES
    (ej. `get_player_recent_matches`) pasan `max_pages=1` para no pedir
    la carrera entera cuando con la página más reciente alcanza.

    Alineado al mismo mecanismo que YA usan `search_player`,
    `_verify_flashscore_player` y `get_match_duration` en este módulo:
    `fetch_with_ip_block_fallback` por página -- request directa primero,
    reintento automático vía proxy BrightData SOLO si Flashscore
    responde 403/429/503 o la conexión falla. `PROXY_FETCH_ENABLED` no
    aplica acá (removido 2026-08-12, ver historial de este archivo).

    Returns: el texto CONCATENADO de todas las páginas fetcheadas (mismo
    shape pipe-delimited que consume `_parse_all_records_with_
    tournament`, funciona igual sobre 1 o N páginas concatenadas --
    `.split("~ZA÷")` no depende de dónde caen los límites de página).
    Fail-closed: `None` si ni siquiera la página 0 responde con éxito."""
    parsed_target = None
    if target_date:
        try:
            parsed_target = (
                datetime.fromisoformat(target_date).date()
                if isinstance(target_date, str) else target_date
            )
        except ValueError:
            parsed_target = None

    headers = {
        "X-Fsign": "SW9D1eZo",
        "Referer": "https://www.flashscore.com/",
        "Accept": "*/*",
    }
    chunks: list[str] = []
    for page in range(max_pages):
        flashscore_throttle.wait()
        url = _PLAYER_RESULTS_FEED_URL.format(pid=pid, page=page)
        resp = brightdata_proxy.fetch_with_ip_block_fallback(url, headers=headers, timeout=25)
        if resp is None or resp.status_code != 200:
            break
        text = (resp.text or "").strip()
        if not text or "~AA÷" not in text:
            break  # página vacía -- fin real de la carrera del jugador
        chunks.append(text)

        if parsed_target is not None:
            timestamps = [int(m) for m in re.findall(r"AD÷(\d+)", text)]
            if timestamps:
                oldest_in_page = datetime.fromtimestamp(min(timestamps), tz=UTC).date()
                if oldest_in_page <= parsed_target:
                    break  # ya cubrimos la fecha buscada, no hace falta seguir

    return "".join(chunks) if chunks else None


# ── Parser de registros ─────────────────────────────────────────────────────

def _field(raw: str, code: str) -> str | None:
    m = re.search(re.escape(code) + r"÷([^¬~\x00-\x1f]*)", raw)
    return m.group(1) if m else None


def _parse_record(raw: str) -> dict[str, Any]:
    rec: dict[str, Any] = {"match_id": raw.split("¬")[0] if raw else None}

    ts = _field(raw, "AD")
    rec["timestamp"] = int(ts) if ts and ts.isdigit() else None

    st = _field(raw, "AB")
    rec["status"] = int(st) if st and st.isdigit() else None  # 3 = finalizado

    rec["p1_name"] = _field(raw, "FH") or _field(raw, "AE") or ""
    rec["p2_name"] = _field(raw, "FK") or _field(raw, "AF") or ""

    ws = _field(raw, "AS")
    rec["winner"] = int(ws) if ws and ws.isdigit() else None

    ag, ah = _field(raw, "AG"), _field(raw, "AH")
    rec["p1_sets"] = int(ag) if ag and ag.isdigit() else None
    rec["p2_sets"] = int(ah) if ah and ah.isdigit() else None

    for i, (c1, c2) in enumerate(_SET_FIELDS, start=1):
        v1, v2 = _field(raw, c1), _field(raw, c2)
        rec[f"p1_s{i}"] = int(v1) if v1 and v1.isdigit() else None
        rec[f"p2_s{i}"] = int(v2) if v2 and v2.isdigit() else None

    return rec


def _parse_all_records(html: str) -> list[dict[str, Any]]:
    raw_blocks = html.split("~AA÷")
    return [_parse_record(rb) for rb in raw_blocks[1:]]  # [0] es el encabezado


def _extract_tournament_name(za_block: str) -> str | None:
    """`za_block` es lo que sigue a un separador `~ZA÷` real, ej:
    'ATP - SINGLES: Barcelona (Spain), clay¬ZEE÷...' -> 'Barcelona'.
    Fail-closed: None si el formato no matchea (nunca adivina)."""
    header = za_block.split("¬", 1)[0]
    m = re.search(r":\s*(.+?)\s*\(", header)
    if m:
        return m.group(1).strip()
    parts = header.split(":", 1)
    if len(parts) == 2:
        name = parts[1].split(",")[0].strip()
        return name or None
    return None


def _parse_all_records_with_tournament(html: str) -> list[dict[str, Any]]:
    """Igual que `_parse_all_records`, pero cada record incluye
    `"tournament"` -- Flashscore agrupa los resultados de un jugador por
    torneo, con un separador `~ZA÷{header del torneo}` antes de cada
    grupo de records `~AA÷` (ver formato real en el docstring del
    módulo). Necesario para `find_match_id` (matching por nombre+fecha+
    torneo, pedido explícito del usuario 2026-08-01)."""
    records: list[dict[str, Any]] = []
    for za_block in html.split("~ZA÷")[1:]:
        tournament = _extract_tournament_name(za_block)
        for aa_block in za_block.split("~AA÷")[1:]:
            rec = _parse_record(aa_block)
            rec["tournament"] = tournament
            records.append(rec)
    return records


def _parse_score_sets(score: str | None) -> list[tuple[int, int]] | None:
    """Parsea un score en formato TennisExplorer ("6-4, 6-2" o "7-5, 2-6,
    6-4 ret.") a una lista de tuplas (games_a, games_b) por set --
    2026-08-11, pedido explícito del usuario ("añade un filtro más para
    validar el match") tras encontrar que `find_match_id` nunca cruzaba
    el marcador real contra el candidato de Flashscore (solo nombres+
    fecha), un gap real que podía elegir el partido equivocado en
    colisiones de fecha cercana entre los mismos 2 jugadores.

    Tolerante a sufijos no numéricos ("ret.", "w.o.") y a tie-breaks
    entre paréntesis (ej. "7-6(4)" -> (7, 6), el tie-break en sí se
    descarta, solo importa quién ganó el set).

    2026-08-12, bug real encontrado en la auditoría de verificación
    ("no pueden haber errores. Verifica nuevamente y resuelve
    definitivamente"): `MatchHistory.score` viene de TennisExplorer con
    el tie-break marcado como HTML `<sup>N</sup>`, y su posición varía
    según qué lado perdió el tie-break -- a veces DESPUÉS del segundo
    número ("7-6<sup>4</sup>", el regex viejo sí matcheaba, se quedaba
    con el prefijo "7-6"), pero a veces ENTRE los dos números
    ("6<sup>6</sup>-7", el ganador del set perdió el tie-break) -- ahí
    el regex viejo (dígitos-guión-dígitos sin tolerar nada en el medio) fallaba
    en seco, ese chunk se DESCARTABA SILENCIOSAMENTE, y `_score_sets_match`
    terminaba comparando listas de distinta longitud (un set entero
    perdido) -- rechazaba partidos REALES por "marcador no coincide".
    Verificado en vivo: 1,061 de 15,753 filas de `MatchHistory` (6.7%)
    tienen este patrón, todas quedaban fail-closed incorrectamente.

    Fix de raíz: se eliminan TODOS los tags `<sup>...</sup>` (con su
    contenido, el tie-break en sí nunca importó para esta comparación)
    del string completo ANTES de partir por sets, sin importar en qué
    posición del chunk caigan. Fail-closed: `None` si `score` viene
    vacío o ningún set parsea con 2 números reales."""
    if not score:
        return None
    score = re.sub(r"<sup>.*?</sup>", "", score, flags=re.DOTALL)
    sets: list[tuple[int, int]] = []
    for chunk in score.split(","):
        m = re.match(r"\s*(\d+)\s*-\s*(\d+)", chunk.strip())
        if m:
            sets.append((int(m.group(1)), int(m.group(2))))
    return sets or None


def _score_sets_match(expected: list[tuple[int, int]], candidate_sets: list[tuple[int, int]]) -> bool:
    """`True` si `candidate_sets` (de Flashscore, orientación p1/p2
    desconocida) coincide con `expected` (de TennisExplorer, ya
    orientado anchor/opponent) en CUALQUIERA de las 2 orientaciones
    posibles (directa o espejada) -- Flashscore no garantiza que su
    "p1" sea el mismo lado que nuestro "anchor". Exige mismo número de
    sets Y mismo resultado de cada uno; no exige el mismo string exacto
    (basta el resultado numérico real de cada set)."""
    if len(expected) != len(candidate_sets):
        return False
    if expected == candidate_sets:
        return True
    return expected == [(b, a) for a, b in candidate_sets]


# ── Matching de nombres ──────────────────────────────────────────────────────

def _norm_name(name: str) -> str:
    return name.lower().replace(".", "").strip()


_NAME_PARTICLES = frozenset({
    "de", "del", "des", "la", "le", "los", "las",
    "van", "von", "der", "den", "dos", "da", "do", "al", "bin", "ibn", "el",
})


def _significant_tokens(name: str) -> set[str]:
    toks = set()
    for t in _norm_name(name).split():
        if len(t) <= 1 or t in _NAME_PARTICLES:
            continue
        toks.add(t)
    return toks


def _name_score(input_name: str, fs_name: str) -> float:
    """Similitud entre el nombre de entrada y el nombre devuelto por
    Flashscore (que usa "Apellido I.", ej. "Djokovic N."). Compara por
    solapamiento de tokens significativos, sin asumir orden nombre/apellido."""
    a, b = _norm_name(input_name), _norm_name(fs_name)
    if a == b:
        return 1.0

    a_toks, b_toks = _significant_tokens(input_name), _significant_tokens(fs_name)
    if a_toks and b_toks:
        overlap = a_toks & b_toks
        if overlap:
            frac = len(overlap) / min(len(a_toks), len(b_toks))
            if frac >= 1.0:
                return 0.9
            if frac >= 0.5:
                return 0.75
            return 0.6

    if len(a) >= 4 and len(b) >= 4 and a[:4] == b[:4]:
        return 0.6
    return 0.0


# ── Función pública: historial reciente de un jugador ──────────────────────

def get_player_recent_matches(player_name: str, n: int = 20) -> list[dict]:
    """Historial reciente de partidos de UN jugador vía Flashscore.

    Returns:
        list de dicts (más reciente primero): {"fecha", "oponente",
        "resultado": "W"|"L", "score", "source": "flashscore"}.
        Fail-closed: [] si el jugador no se encuentra, la request falla,
        o no hay partidos finalizados parseables (nunca fabrica resultados).
    """
    found = search_player(player_name)
    if not found:
        last = player_name.split()[-1] if player_name else ""
        if last and last != player_name:
            found = search_player(last)
    if not found:
        return []

    pid, slug = found
    # 2026-08-12: esta función solo quiere `n` (default 20) partidos
    # RECIENTES -- a diferencia de find_match_id/find_match_tournament
    # (que necesitan poder llegar a cualquier fecha del historial
    # completo), acá no tiene sentido paginar toda la carrera.
    # `max_pages=1` (~40-45 partidos más recientes, verificado real)
    # sobra para cualquier `n` razonable de este caller, y evita pedirle
    # a Flashscore toda la carrera del jugador en cada llamada.
    html = _fetch_results_page(pid, slug, max_pages=1)
    if not html:
        return []

    out: list[dict] = []
    for rec in _parse_all_records(html):
        if rec.get("status") != 3 or not rec.get("timestamp"):
            continue

        p1_fs, p2_fs = rec.get("p1_name", ""), rec.get("p2_name", "")
        score_p1, score_p2 = _name_score(player_name, p1_fs), _name_score(player_name, p2_fs)
        if score_p1 < 0.6 and score_p2 < 0.6:
            continue
        is_p1 = score_p1 >= score_p2
        opponent = p2_fs if is_p1 else p1_fs

        winner_pos = rec.get("winner")
        p1_sets, p2_sets = rec.get("p1_sets"), rec.get("p2_sets")
        if winner_pos is None and p1_sets is not None and p2_sets is not None:
            if p1_sets > p2_sets:
                winner_pos = 1
            elif p2_sets > p1_sets:
                winner_pos = 2
        if winner_pos is None:
            continue  # no se puede determinar ganador (retiro con sets iguales, etc.)

        won = (winner_pos == 1) == is_p1

        set_scores: list[tuple[int, int]] = []
        for i in range(1, 6):
            g1, g2 = rec.get(f"p1_s{i}"), rec.get(f"p2_s{i}")
            if g1 is None:
                break
            set_scores.append((g1, g2) if is_p1 else (g2, g1))
        score_str = " ".join(f"{a}-{b}" for a, b in set_scores)

        rec_date = datetime.fromtimestamp(rec["timestamp"], tz=UTC).date()
        out.append({
            "fecha": rec_date.isoformat(),
            "oponente": opponent,
            "resultado": "W" if won else "L",
            "score": score_str,
            "source": "flashscore",
        })
        if len(out) >= n:
            break

    return out


# ── Estadísticas reales de partido (2026-08-01, pedido explícito del usuario:
# "cuando se detecte que un partido terminó se debe hacer fetch de las
# stats") ────────────────────────────────────────────────────────────────

_STATS_URL = "https://global.flashscore.ninja/2/x/feed/df_st_1_{match_id}"


def find_match_tournament(player_a_name: str, player_b_name: str, match_date: str) -> str | None:
    """Recupera el nombre real del torneo de un partido YA JUGADO por
    coincidencia de AMBOS nombres + fecha (mismo criterio de matching que
    `find_match_id`, sin el bonus de torneo porque acá es justamente lo
    que se busca) -- pedido explícito del usuario 2026-08-02, para
    partidos donde `extract_tournament_name(rules_secondary)` de Kalshi
    falló (típicamente Grand Slams, cuyo `rules_secondary` no sigue el
    mismo formato que los torneos ATP/WTA regulares).

    NO valida duplicados entre distintos partidos del mismo día -- eso
    ya lo garantiza `find_match_id`/este mismo matching (nombre+fecha
    real de AMBOS jugadores es prácticamente única para singles; dobles
    tendría 4 nombres, fuera de alcance de este módulo que es
    exclusivamente singles).

    Fail-closed: None si no se encuentra a ningún jugador, la request
    falla, o ningún record matchea nombre+fecha con confianza
    suficiente."""
    found = search_player(player_a_name)
    anchor_name, opponent_name = player_a_name, player_b_name
    if not found:
        found = search_player(player_b_name)
        anchor_name, opponent_name = player_b_name, player_a_name
    if not found:
        return None

    try:
        target_date = datetime.fromisoformat(match_date).date() if isinstance(match_date, str) else None
    except ValueError:
        target_date = None
    if target_date is None:
        return None

    pid, slug = found
    # 2026-08-12: `target_date` pasado a `_fetch_results_page` para
    # corte temprano -- pagina hasta cubrir la fecha buscada, sin traer
    # la carrera entera cuando el partido es reciente.
    html = _fetch_results_page(pid, slug, target_date=match_date)
    if not html:
        return None

    best_tournament = None
    best_score = 0.0
    for rec in _parse_all_records_with_tournament(html):
        if rec.get("status") != 3 or not rec.get("timestamp"):
            continue

        p1_fs, p2_fs = rec.get("p1_name", ""), rec.get("p2_name", "")
        score_anchor_p1, score_anchor_p2 = _name_score(anchor_name, p1_fs), _name_score(anchor_name, p2_fs)
        score_anchor = max(score_anchor_p1, score_anchor_p2)
        if score_anchor < 0.6:
            continue
        opponent_fs = p2_fs if score_anchor_p1 >= score_anchor_p2 else p1_fs
        score_opponent = _name_score(opponent_name, opponent_fs)
        if score_opponent < 0.6:
            continue

        rec_date = datetime.fromtimestamp(rec["timestamp"], tz=UTC).date()
        if abs((rec_date - target_date).days) > 1:
            continue

        combined = score_anchor + score_opponent
        if combined > best_score:
            best_score = combined
            best_tournament = rec.get("tournament")

    return best_tournament


def find_match_id(
    player_a_name: str, player_b_name: str, match_date: str, tournament: str | None = None,
    expected_score: str | None = None,
) -> str | None:
    """Busca el `match_id` real de Flashscore para un partido YA JUGADO,
    por coincidencia de AMBOS nombres + fecha (+ torneo como bonus,
    best-effort, ver `_extract_tournament_name`) -- necesario para poder
    pedir después las estadísticas vía `get_match_statistics`.

    `match_date`: fecha ISO (YYYY-MM-DD), típicamente `close_time` de
    Kalshi -- se acepta +/-1 día de margen porque el timestamp de cierre
    del mercado no necesariamente cae en el mismo día calendario que
    Flashscore registra según la zona horaria del torneo.

    Busca primero por `player_a_name`; si Flashscore no lo encuentra
    (nombre no indexado, típico de jugadores de bajo ranking), reintenta
    anclado en `player_b_name` -- cualquiera de los 2 tiene el mismo
    partido en su página de resultados.

    `expected_score`: 2026-08-11, pedido explícito del usuario ("añade
    un filtro más para validar el match") -- si el caller YA conoce el
    marcador real (ej. `MatchHistory.score`, ya resuelto vía
    TennisExplorer), se exige que el candidato de Flashscore tenga el
    MISMO resultado de sets (ver `_score_sets_match`) -- cierra un gap
    real: antes solo se validaba nombre+fecha (±1 día), lo que podía
    elegir el partido equivocado si los mismos 2 jugadores se cruzaron
    2 veces en fechas cercanas (ej. torneos round-robin). Opcional
    (`None` por default) -- si no se pasa, o no parsea, el filtro se
    omite (comportamiento previo intacto para callers sin el dato).

    Fail-closed: None si no se encuentra a ningún jugador, la request
    falla, o ningún record matchea nombre+fecha (+marcador, si se pasa)
    con confianza suficiente -- nunca adivina un match_id."""
    expected_sets = _parse_score_sets(expected_score)
    found = search_player(player_a_name)
    anchor_name, opponent_name = player_a_name, player_b_name
    if not found:
        found = search_player(player_b_name)
        anchor_name, opponent_name = player_b_name, player_a_name
    if not found:
        return None

    try:
        target_date = datetime.fromisoformat(match_date).date() if isinstance(match_date, str) else None
    except ValueError:
        target_date = None
    if target_date is None:
        return None

    pid, slug = found
    # 2026-08-12: `target_date` pasado a `_fetch_results_page` para
    # corte temprano -- pagina hasta cubrir la fecha buscada, sin traer
    # la carrera entera cuando el partido es reciente.
    html = _fetch_results_page(pid, slug, target_date=match_date)
    if not html:
        return None

    best_match_id = None
    best_score = 0.0
    for rec in _parse_all_records_with_tournament(html):
        if rec.get("status") != 3 or not rec.get("timestamp"):
            continue  # solo partidos finalizados, con timestamp real

        p1_fs, p2_fs = rec.get("p1_name", ""), rec.get("p2_name", "")
        score_anchor_p1, score_anchor_p2 = _name_score(anchor_name, p1_fs), _name_score(anchor_name, p2_fs)
        score_anchor = max(score_anchor_p1, score_anchor_p2)
        if score_anchor < 0.6:
            continue
        opponent_fs = p2_fs if score_anchor_p1 >= score_anchor_p2 else p1_fs
        score_opponent = _name_score(opponent_name, opponent_fs)
        if score_opponent < 0.6:
            continue

        rec_date = datetime.fromtimestamp(rec["timestamp"], tz=UTC).date()
        if abs((rec_date - target_date).days) > 1:
            continue  # margen de +/-1 dia por husos horarios distintos

        if expected_sets is not None:
            candidate_sets = [
                (rec[f"p1_s{i}"], rec[f"p2_s{i}"])
                for i in range(1, 6)
                if rec.get(f"p1_s{i}") is not None and rec.get(f"p2_s{i}") is not None
            ]
            if not candidate_sets or not _score_sets_match(expected_sets, candidate_sets):
                continue  # marcador no coincide -- NO es el mismo partido, se descarta

        combined = score_anchor + score_opponent
        if tournament and rec.get("tournament"):
            if _norm_name(tournament) in _norm_name(rec["tournament"]) or \
               _norm_name(rec["tournament"]) in _norm_name(tournament):
                combined += 0.5  # bonus, no obligatorio -- el torneo puede no matchear textualmente

        if combined > best_score:
            best_score = combined
            best_match_id = rec.get("match_id")

    return best_match_id


def get_match_statistics(match_id: str) -> dict[str, dict[str, tuple[str | None, str | None]]] | None:
    """Estadísticas reales de un partido ya jugado (aces, 1er saque%,
    dobles faltas, break points salvados/convertidos, etc.), vía el feed
    `df_st_1` de Flashscore.

    Formato crudo (pipe-delimited, reverse-engineered 2026-08-01):
    bloques separados por '¬~', cada bloque es uno de:
      'SE÷{Match|Set N}'      -- nueva sección (partido completo o set individual)
      'SF÷{categoría}'        -- Service/Return/Points/Games, informativo, no se guarda aparte
      'SG÷{nombre}¬SH÷{p1}¬SI÷{p2}' -- una estadística real, valor jugador 1 y 2

    Returns:
        {"match": {stat_name: (p1_value, p2_value), ...}, "set_1": {...},
        ...} -- valores como strings tal cual los da Flashscore (algunos
        son porcentajes tipo "58% (31/53)", no siempre numéricos puros,
        se guardan crudos a propósito). Fail-closed: None si falla la
        request o el partido no tiene estadísticas trackeadas (común en
        challenger/ITF de bajo nivel) -- nunca fabrica valores.

    2026-08-16, pedido explícito del usuario ("sí, el gate de
    estadísticas sin proxy"): se REMUEVE el gate duro
    `if not PROXY_FETCH_ENABLED: return None` que existía acá desde la
    pausa del proxy del 2026-08-04, y se alinea al mismo mecanismo que
    ya usan `search_player`/`get_match_duration`/`_fetch_results_page`:
    `fetch_with_ip_block_fallback` (directo primero, proxy SOLO ante una
    señal real de bloqueo -- 403/429/503 o error de conexión).

    POR QUÉ: el gate no estaba protegiendo nada. Medido en producción
    2026-08-16, `tennis_match_stats` tenía 0 filas pese a que el
    pipeline completo (`tennis_match_history_sync_worker` ->
    `get_match_statistics` -> `save_match_stats_for_history`) ya estaba
    wireado y corriendo: esta función cortaba en la primera línea.
    Verificado en vivo contra 3 `match_id` reales de la base
    (`hAvW0ePG`, `fg9MFEGD`, `hQjR703N`): el endpoint responde HTTP 200
    con datos reales SIN proxy. El gate estaba bloqueando datos gratis
    y disponibles, y con ellos toda la estadística de saque que alimenta
    los modelos (`models/volatility/sports/tennis/tennis_markov.py`)."""
    flashscore_throttle.wait()
    url = _STATS_URL.format(match_id=match_id)
    headers = {
        "X-Fsign": "SW9D1eZo",
        "Referer": "https://www.flashscore.com/",
        "Accept": "*/*",
    }

    resp = brightdata_proxy.fetch_with_ip_block_fallback(url, headers=headers, timeout=25)
    if resp is None or resp.status_code != 200:
        return None
    text = (resp.text or "").strip()
    if not text or text == "0":
        return None

    sections: dict[str, dict[str, tuple[str | None, str | None]]] = {}
    current_key: str | None = None
    set_count = 0
    for chunk in text.split("¬~"):
        parts = chunk.split("¬")
        head = parts[0]
        if head.startswith("SE÷"):
            label = head[len("SE÷"):].strip()
            if label.lower() == "match":
                current_key = "match"
            else:
                set_count += 1
                current_key = f"set_{set_count}"
            sections[current_key] = {}
        elif head.startswith("SG÷") and current_key is not None:
            name = head[len("SG÷"):].strip()
            if not name:
                continue
            p1_val = p2_val = None
            for p in parts[1:]:
                if p.startswith("SH÷"):
                    p1_val = p[len("SH÷"):]
                elif p.startswith("SI÷"):
                    p2_val = p[len("SI÷"):]
            sections[current_key][name] = (p1_val, p2_val)

    return sections if sections else None


# ── Punto a punto + quiebres (sin proxy) ───────────────────────────────────

_POINT_BY_POINT_URL = "https://global.flashscore.ninja/2/x/feed/df_mh_1_{match_id}"


def get_point_by_point(match_id: str) -> dict[str, Any] | None:
    """Punto a punto real de un partido ya jugado, vía el feed `df_mh_1`
    de Flashscore -- 2026-08-10, pedido explícito del usuario ("amplía
    el scraper de flashscore para tener el punto a punto y los
    quiebres"). Internamente Flashscore llama a esto "Match History"
    (visible en el config de la página: `..._IDENT_MATCH_HISTORY_TENNIS`);
    la UI de tenis lo relabela como "point-by-point", de ahí el nombre
    de la función.

    Confirmado en vivo SIN proxy (a diferencia de `get_match_statistics`
    -- ver `PROXY_FETCH_ENABLED`): este endpoint respondió 200 con
    `requests.get` directo, mismo patrón que `_verify_flashscore_player`
    (fetch puntual, no bulk scraping de `/results/`). Se usa fetch
    directo a propósito, no gateado por `PROXY_FETCH_ENABLED`.

    Formato crudo (pipe-delimited, reverse-engineered 2026-08-10):
    bloques separados por '¬~':
      'HA÷{Set N}¬HB÷{Point by point - Set N}' -- header de set nuevo.
      'HC÷..¬HE÷..¬HG÷..¬HH÷..¬HK÷{1|2}¬HL÷{secuencia}' -- un juego:
        - HK: lado que GANÓ el juego (1 o 2). CONFIRMADO en vivo --
          sumando HK por set contra el marcador real de sets
          (Rybakina/Samsonova, match QivlICam) coincidió exacto
          (4-6, 6-4, 4-6).
        - HL: secuencia de tanteo punto a punto dentro del juego
          (ej. "0:15, 15:15, 30:15, 40:15"), con marcadores inline
          `|B1|`/`|B2|`/`|B3|` en el punto donde hubo BREAK POINT --
          esto es lo que pidió el usuario explícitamente ("qué puntos
          se dieron antes de un quiebre"), se preserva tal cual.
        - HC/HE/HG/HH: NO completamente decodificados con certeza
          (parecen algún tipo de contador de juegos/sets, pero su
          semántica exacta no se pudo confirmar con los datos
          disponibles -- se guardan crudos, sin inventar significado,
          por si un análisis futuro los necesita).
      'A1÷' -- marcador de fin de feed, ignorado.

    Returns:
        {"set_1": {"label": "Point by point - Set 1", "games": [
            {"winner_side": 1, "points": ["0:15", "15:15", ...],
             "break_points": [{"point_index": 3, "sequence_raw": "40:15"}],
             "raw": {"HC": "1", "HE": "0", "HG": "2", "HH": "2"}},
            ...
        ]}, "set_2": {...}, ...} -- Fail-closed: None si falla la
        request, el partido no tiene punto a punto trackeado, o el feed
        viene vacío. Nunca fabrica valores.

        2026-08-11, pedido explícito del usuario ("los scrapers deben
        tener la característica de detectar cuando los sitios bloquean
        posiblemente por IP") -- fallback a proxy ante 403/429/503 o
        excepción de red, ver docstring de `_verify_flashscore_player`."""
    try:
        flashscore_throttle.wait()
        url = _POINT_BY_POINT_URL.format(match_id=match_id)
        resp = brightdata_proxy.fetch_with_ip_block_fallback(
            url,
            headers={
                "X-Fsign": "SW9D1eZo",
                "Referer": "https://www.flashscore.com/",
                "Accept": "*/*",
            },
            timeout=25,
        )
        if resp is None or resp.status_code != 200:
            return None
        text = (resp.text or "").strip()
        if not text or text == "0":
            return None
    except Exception:
        return None

    sets: dict[str, Any] = {}
    current_key: str | None = None
    set_count = 0
    for chunk in text.split("¬~"):
        parts = chunk.split("¬")
        head = parts[0]
        if head.startswith("HA÷"):
            set_count += 1
            current_key = f"set_{set_count}"
            label = None
            for p in parts[1:]:
                if p.startswith("HB÷"):
                    label = p[len("HB÷"):].strip()
            sets[current_key] = {"label": label, "games": []}
        elif head.startswith("HC÷") and current_key is not None:
            raw: dict[str, str] = {}
            winner_side = None
            sequence = None
            for p in parts:
                if p.startswith("HC÷"):
                    raw["HC"] = p[len("HC÷"):]
                elif p.startswith("HE÷"):
                    raw["HE"] = p[len("HE÷"):]
                elif p.startswith("HG÷"):
                    raw["HG"] = p[len("HG÷"):]
                elif p.startswith("HH÷"):
                    raw["HH"] = p[len("HH÷"):]
                elif p.startswith("HK÷"):
                    winner_side = p[len("HK÷"):].strip()
                elif p.startswith("HL÷"):
                    sequence = p[len("HL÷"):].strip()
            if sequence is None:
                continue
            points = [s.strip() for s in sequence.split(",") if s.strip()]
            break_points = []
            for idx, point in enumerate(points):
                if "|B" in point:
                    break_points.append({"point_index": idx, "sequence_raw": point})
            sets[current_key]["games"].append({
                "winner_side": int(winner_side) if winner_side and winner_side.isdigit() else None,
                "points": points,
                "break_points": break_points,
                "raw": raw,
            })

    return sets if sets else None


# ── Duración del partido + sets (sin proxy) ────────────────────────────────

_SUMMARY_URL = "https://global.flashscore.ninja/2/x/feed/df_su_1_{match_id}"

# 2026-08-12, pedido explícito del usuario ("necesitamos la duración de
# los matches... está en tennis explorer[?]") -- TennisExplorer NO
# expone duración (verificado exhaustivamente en vivo: sin campo
# "Duration" ni patrón H:MM oculto en un partido real ya backfillado,
# Sinner-Alcaraz Masters Cup 2025). Flashscore SÍ la trae, feed
# `df_su_1` (Summary), reverse-engineered 2026-08-10, re-verificado en
# vivo 2026-08-12 contra el mismo partido real usado para `get_point_by_
# point` (Rybakina/Samsonova, match_id "QivlICam"): `RB÷{total}` +
# `RC÷{set1}`/`RD÷{set2}`/`RE÷{set3}`/`RF÷{set4}`/`RG÷{set5}`, formato
# "H:MM" -- confirmado que la suma de sets (0:59+0:53+1:03=175min)
# coincide EXACTO con el total reportado (RB÷2:55=175min), no es una
# coincidencia de formato.
_DURATION_SET_CODES = ("RC", "RD", "RE", "RF", "RG")


def _parse_hmm_to_minutes(raw: str | None) -> int | None:
    """"2:55" -> 175 (horas*60 + minutos). Fail-closed: None si no
    matchea exactamente el formato "H:MM" esperado."""
    if not raw:
        return None
    m = re.match(r"^(\d+):(\d{2})$", raw.strip())
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def get_match_duration(match_id: str) -> dict[str, Any] | None:
    """Duración real de un partido ya jugado (total + por set), vía el
    feed `df_su_1` de Flashscore -- ver comentario arriba del módulo
    para el reverse-engineering completo. Fetch directo con fallback a
    proxy ante posible bloqueo por IP, mismo criterio que el resto de
    este módulo (`fetch_with_ip_block_fallback`).

    Returns: {"total_minutes": 175, "set_1_minutes": 59,
    "set_2_minutes": 53, "set_3_minutes": 63, "set_4_minutes": None,
    "set_5_minutes": None} -- sets sin jugar (partido de menos de 5
    sets) quedan en `None`, nunca se fabrica un valor. Fail-closed:
    `None` si falla la request, el partido no tiene duración trackeada,
    o el feed viene vacío."""
    try:
        flashscore_throttle.wait()
        url = _SUMMARY_URL.format(match_id=match_id)
        resp = brightdata_proxy.fetch_with_ip_block_fallback(
            url,
            headers={
                "X-Fsign": "SW9D1eZo",
                "Referer": "https://www.flashscore.com/",
                "Accept": "*/*",
            },
            timeout=25,
        )
        if resp is None or resp.status_code != 200:
            return None
        text = (resp.text or "").strip()
        if not text or text == "0":
            return None
    except Exception:
        return None

    result: dict[str, int | None] = {
        "total_minutes": None,
        "set_1_minutes": None, "set_2_minutes": None, "set_3_minutes": None,
        "set_4_minutes": None, "set_5_minutes": None,
    }
    for chunk in text.split("¬~"):
        for p in chunk.split("¬"):
            if p.startswith("RB÷"):
                result["total_minutes"] = _parse_hmm_to_minutes(p[len("RB÷"):])
            else:
                for i, code in enumerate(_DURATION_SET_CODES, start=1):
                    if p.startswith(f"{code}÷"):
                        result[f"set_{i}_minutes"] = _parse_hmm_to_minutes(p[len(code) + 1:])

    if result["total_minutes"] is None and all(result[f"set_{i}_minutes"] is None for i in range(1, 6)):
        return None
    return result
