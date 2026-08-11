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
# hasta nuevo aviso para fetchs" -- migramos historial/H2H a
# TennisExplorer (sin proxy, ver match_history.py), pero
# find_match_id/get_match_statistics (estadísticas detalladas de
# partido) SIGUEN sin reemplazo real y requieren proxy BrightData
# (Flashscore bloquea esas 2 páginas sin proxy). En vez de borrar esa
# estructura (el worker sigue revisando partidos nuevos sin stats,
# `has_match_stats`/`_fetch_and_save_match_stats` intactos), se pausa
# el fetch en el punto más bajo posible -- reversible con una env var,
# sin tocar código, cuando el usuario avise que se puede retomar.
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
    significativo real con `expected_name`."""
    try:
        import requests

        flashscore_throttle.wait()
        url = _PROFILE_URL.format(slug=slug, pid=pid)
        resp = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://www.flashscore.com/",
            },
            timeout=10,
        )
        if resp.status_code != 200:
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
        import requests

        # 2026-08-04, pedido explícito -- throttle real ANTES de la
        # request (no antes del cache-hit de arriba, para no penalizar
        # nombres ya conocidos), "no overhittear los servers".
        flashscore_throttle.wait()
        url = _SEARCH_URL.format(name=name.replace(" ", "%20"))
        resp = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://www.flashscore.com/",
            },
            timeout=10,
        )
        m = re.search(r"\((.+)\)", resp.text, re.DOTALL)
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


# ── Fetch de resultados (con proxy BrightData) ─────────────────────────────

def _fetch_results_page(pid: str, slug: str) -> str | None:
    """Obtiene la página de resultados del jugador vía proxy BrightData.
    Fail-closed: None si BrightData no está configurado o falla la
    request (nunca intenta sin proxy, Flashscore bloquea esta página),
    o si `PROXY_FETCH_ENABLED` está en False (pausado, ver docstring del
    módulo -- 2026-08-04)."""
    if not PROXY_FETCH_ENABLED:
        return None
    flashscore_throttle.wait()
    url = _RESULTS_URL.format(slug=slug, pid=pid)
    headers = {
        "X-Fsign": "SW9D1eZo",
        "Referer": "https://www.flashscore.com/",
        "Accept": "text/html,application/xhtml+xml,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }

    def _do_request(session):
        # verify=False: BrightData's residential proxy does TLS
        # interception, presenting a self-signed cert in the chain --
        # confirmed necessary (CertificateVerifyError without it), same
        # as the pre-makeover rescued code did.
        return session.get(url, headers=headers, timeout=25, verify=False)

    resp = brightdata_proxy.request_via_proxy_with_ip_retry(
        brightdata_proxy.make_proxy_session, _do_request,
    )
    if resp is not None and resp.status_code == 200:
        return resp.text
    return None


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
    html = _fetch_results_page(pid, slug)
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

    pid, slug = found
    html = _fetch_results_page(pid, slug)
    if not html:
        return None

    try:
        target_date = datetime.fromisoformat(match_date).date() if isinstance(match_date, str) else None
    except ValueError:
        target_date = None
    if target_date is None:
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

    Fail-closed: None si no se encuentra a ningún jugador, la request
    falla, o ningún record matchea nombre+fecha con confianza suficiente
    -- nunca adivina un match_id."""
    found = search_player(player_a_name)
    anchor_name, opponent_name = player_a_name, player_b_name
    if not found:
        found = search_player(player_b_name)
        anchor_name, opponent_name = player_b_name, player_a_name
    if not found:
        return None

    pid, slug = found
    html = _fetch_results_page(pid, slug)
    if not html:
        return None

    try:
        target_date = datetime.fromisoformat(match_date).date() if isinstance(match_date, str) else None
    except ValueError:
        target_date = None
    if target_date is None:
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
        challenger/ITF de bajo nivel), o si `PROXY_FETCH_ENABLED` está en
        False (pausado, ver docstring del módulo -- 2026-08-04) -- nunca
        fabrica valores."""
    if not PROXY_FETCH_ENABLED:
        return None
    flashscore_throttle.wait()
    url = _STATS_URL.format(match_id=match_id)
    headers = {
        "X-Fsign": "SW9D1eZo",
        "Referer": "https://www.flashscore.com/",
        "Accept": "*/*",
    }

    def _do_request(session):
        return session.get(url, headers=headers, timeout=25, verify=False)

    resp = brightdata_proxy.request_via_proxy_with_ip_retry(
        brightdata_proxy.make_proxy_session, _do_request,
    )
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
