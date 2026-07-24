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

import re
import time
from datetime import datetime, UTC
from typing import Any

from outer_apis.scrapers import brightdata_proxy

_SEARCH_URL = "https://s.flashscore.com/search/?q={name}&l=1&s=2&f=1%3B1&pid=2&sid=2"
_RESULTS_URL = "https://www.flashscore.com/player/{slug}/{pid}/results/"

_SET_FIELDS = [("BA", "BB"), ("BC", "BD"), ("BE", "BF"), ("BG", "BH"), ("BI", "BJ")]

_player_id_cache: dict[str, tuple[str, str] | None] = {}


# ── Búsqueda de jugador (sin proxy) ────────────────────────────────────────

def search_player(name: str) -> tuple[str, str] | None:
    """Busca un jugador de tenis por nombre en la API pública de
    Flashscore (endpoint público JSON/JSONP, sin proxy).

    Returns:
        (participant_id, url_slug) o None si no se encuentra / falla.
    """
    cache_key = name.lower().strip()
    if cache_key in _player_id_cache:
        return _player_id_cache[cache_key]

    result = None
    try:
        import requests

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
                    result = (item["id"], item.get("url", ""))
                    break
    except Exception:
        result = None

    _player_id_cache[cache_key] = result
    return result


# ── Fetch de resultados (con proxy BrightData) ─────────────────────────────

def _fetch_results_page(pid: str, slug: str) -> str | None:
    """Obtiene la página de resultados del jugador vía proxy BrightData.
    Fail-closed: None si BrightData no está configurado o falla la
    request (nunca intenta sin proxy, Flashscore bloquea esta página)."""
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
