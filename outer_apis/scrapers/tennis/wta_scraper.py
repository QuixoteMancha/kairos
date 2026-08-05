"""
WTA Tour official-site scraper (api.wtatennis.com JSON API — no HTML
parsing needed, unlike ATP).

Rescued from the pre-"extreme makeover" architecture
(`src/scrapers/atp_wta_scraper.py`, deleted commit `02d7f35`; original
archive commit `17d71b8`), re-verified live 2026-07-20, code unchanged
(the API responded correctly with the original headers, no fix needed —
unlike ATP's rankings HTML page which needed extra headers to dodge a
Cloudflare 403).

This module ONLY does raw HTTP + JSON parsing (no business logic, no
caching policy) — the public call surface lives in `outer_apis/tennis_rankings.py`
per the project's separation: scrapers/ = raw source access,
outer_apis/<facade>.py = call methods used by the rest of Kairos.
"""

import difflib
import re
import unicodedata
from datetime import date
from typing import Any

import requests

_API_PLAYERS = "https://api.wtatennis.com/tennis/players"
_API_RANKING = "https://api.wtatennis.com/tennis/players/{player_id}/ranking"
_API_RANKED = "https://api.wtatennis.com/tennis/players/ranked"
_PAGE_SIZE = 100

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, */*;q=0.8",
    "Referer": "https://www.wtatennis.com/rankings/singles",
    "Origin": "https://www.wtatennis.com",
}

_REQUEST_TIMEOUT_S = 15


def _get(url: str, params: dict | None = None) -> requests.Response | None:
    """Fail-closed: any network error or non-200 status -> None."""
    try:
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=_REQUEST_TIMEOUT_S)
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    return resp


def _norm(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9 ]", " ", ascii_str.lower()).strip()


def get_top_rankings(n: int = 200) -> list[dict[str, Any]]:
    """Top-N WTA singles players from the official ranked-players API.
    Fail-closed: any fetch/parse failure returns whatever was
    successfully collected so far (possibly empty), never raises."""
    result: list[dict[str, Any]] = []
    page = 0
    today = date.today().isoformat()

    while len(result) < n:
        resp = _get(
            _API_RANKED,
            params={
                "page": page,
                "pageSize": _PAGE_SIZE,
                "type": "rankSingles",
                "sort": "asc",
                "metric": "SINGLES",
                "at": today,
            },
        )
        if not resp:
            break
        try:
            items = resp.json()
        except ValueError:
            break
        if not isinstance(items, list) or not items:
            break

        for item in items:
            player_obj = item.get("player", {})
            name = player_obj.get("fullName", "")
            rank = item.get("ranking")
            if not name or rank is None or int(rank) > n:
                continue
            result.append({
                "nombre": name,
                "ranking": int(rank),
                "ranking_type": "WTA",
                "pais": player_obj.get("countryCode"),
            })

        if len(items) < _PAGE_SIZE:
            break
        page += 1

    result.sort(key=lambda x: x["ranking"])
    return result[:n]


def get_ranked_players_at(at_date: str, n: int = 300) -> list[dict[str, Any]]:
    """Como `get_top_rankings` pero para una fecha HISTÓRICA real
    (`at_date`, "YYYY-MM-DD") -- 2026-08-05, pedido explícito del
    usuario ("calcular los resultados de esta estrategia tanto en ATP
    como WTA"). La API oficial de WTA acepta `at=` con cualquier fecha
    pasada (verificado en vivo), no solo la actual -- usado para armar
    un roster amplio de jugadoras reales que estuvieron rankeadas en
    distintos puntos del rango de evaluación (2016-2026), no solo las
    top-N de HOY (que perderían a cualquier jugadora ya retirada).

    A diferencia de `get_top_rankings`, este SÍ incluye `id` (necesario
    para `get_weekly_ranking_history`). Fail-closed: [] ante cualquier
    fallo de red/parseo."""
    result: list[dict[str, Any]] = []
    page = 0
    while len(result) < n:
        resp = _get(
            _API_RANKED,
            params={
                "page": page, "pageSize": _PAGE_SIZE, "type": "rankSingles",
                "sort": "asc", "metric": "SINGLES", "at": at_date,
            },
        )
        if not resp:
            break
        try:
            items = resp.json()
        except ValueError:
            break
        if not isinstance(items, list) or not items:
            break

        for item in items:
            player_obj = item.get("player", {})
            player_id = player_obj.get("id")
            name = player_obj.get("fullName", "")
            rank = item.get("ranking")
            if not name or player_id is None or rank is None or int(rank) > n:
                continue
            result.append({"id": player_id, "name": name, "ranking": int(rank)})

        if len(items) < _PAGE_SIZE:
            break
        page += 1

    result.sort(key=lambda x: x["ranking"])
    return result[:n]


def get_weekly_ranking_history(player_id: int) -> list[dict[str, Any]]:
    """Serie REAL de rankings semanales de una jugadora, ranking oficial
    WTA -- 2026-08-05, pedido explícito del usuario. Verificado en vivo
    (Sabalenka, id 320760): 599 entradas semanales reales, desde
    2012-11-19 hasta hoy -- esta API ya traía el histórico completo, el
    código previo (`_get_current_ranking`) solo tomaba la más reciente.

    Returns: lista de {"ranked_at": "YYYY-MM-DD", "singles_ranking": int}
    ordenada cronológicamente ascendente. Fail-closed: [] ante fallo de
    red/parseo, entradas sin `rankedAt`/`singlesRanking` real se
    descartan (nunca se fabrica un ranking)."""
    resp = _get(_API_RANKING.format(player_id=player_id))
    if not resp:
        return []
    try:
        data = resp.json()
    except ValueError:
        return []
    weekly = data.get("weeklyRankings", [])
    result = []
    for w in weekly:
        ranked_at = w.get("rankedAt")
        ranking = w.get("singlesRanking")
        if not ranked_at or ranking is None:
            continue
        result.append({"ranked_at": ranked_at[:10], "singles_ranking": int(ranking)})
    result.sort(key=lambda r: r["ranked_at"])
    return result


def get_player_data(name: str) -> dict[str, Any] | None:
    """Fail-closed: returns None if the player isn't found or on any
    fetch/parse failure. Never fabricates a match."""
    player_id, player_info = _find_player_id(name)
    if not player_id:
        return None
    ranking = _get_current_ranking(player_id)
    return {
        "nombre": player_info.get("fullName", name),
        "ranking": ranking,
        "ranking_type": "WTA",
        "pais": player_info.get("countryCode"),
        "_source": "wta_api",
        "_wta_id": player_id,
    }


def _find_player_id(name: str) -> tuple[int | None, dict]:
    parts = name.strip().split()
    last = parts[-1] if parts else name
    resp = _get(_API_PLAYERS, params={"name": last})
    if not resp:
        return None, {}
    try:
        data = resp.json()
    except ValueError:
        return None, {}
    candidates = data.get("content", [])
    target_norm = _norm(name)
    best, best_score = None, 0.0
    for p in candidates:
        pnorm = _norm(p.get("fullName", ""))
        score = difflib.SequenceMatcher(None, target_norm, pnorm).ratio()
        if score > best_score:
            best_score, best = score, p
    if best and best_score >= 0.72:
        return best["id"], best
    return None, {}


def _get_current_ranking(player_id: int) -> int | None:
    resp = _get(_API_RANKING.format(player_id=player_id))
    if not resp:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    weekly = data.get("weeklyRankings", [])
    if not weekly:
        return None
    weekly_sorted = sorted(weekly, key=lambda w: w.get("rankedAt", ""), reverse=True)
    return weekly_sorted[0].get("singlesRanking")
