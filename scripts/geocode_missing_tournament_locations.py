"""Geocoding real de la cola larga de torneos ITF/challenger sin
ubicación conocida -- 2026-08-04, pedido explícito del usuario:
"investiga de donde se pueden sacar más datos de itf/challenger para
completar la data".

FUENTE: Nominatim (OpenStreetMap), API pública gratis, sin clave,
límite real documentado 1 request/segundo, requiere User-Agent real
identificable (verificado en la política de uso oficial). A diferencia
de la tabla estática `tournament_location.py` (curada a mano, torneos
grandes con sede fija conocida de memoria), esta cola son CIENTOS de
torneos challenger/ITF individuales -- demasiados para catalogar uno
por uno, pero la mayoría YA lleva el nombre real de su ciudad sede en
el nombre del torneo (ej. "Prostejov challenger" -> Prostejov,
República Checa) -- geocodificar la ciudad extraída es un dato REAL, no
una adivinanza (distinto de inventar una ubicación aproximada por
similitud de nombre, que sigue prohibido).

EXCLUSIÓN DELIBERADA: eventos de equipo/liga (Bundesliga, Davis Cup,
Billie Jean King Cup, campeonatos nacionales por equipos) NO tienen una
sede única real -- se juegan en distintas ciudades según la ronda/año.
Geocodificarlos asignaría una ciudad falsa. `_BLOCKLIST_KEYWORDS` los
excluye explícitamente, quedan fail-closed (sin ubicación) a propósito.

Solo geocodifica CANDIDATOS DE CIUDAD distintos (dedupe real -- muchos
torneos distintos comparten la misma ciudad sede a través de los años,
ej. "Cluj-Napoca WTA" y "Cluj-Napoca 2 WTA"), nunca re-consulta un
candidato que ya está en `TournamentLocationCache` (haya resuelto o
no) -- respeta el límite real de 1 req/s sin re-trabajo."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import requests

from database.db import init_db_historical
from database.historical.tennis import TennisMatchHistory, TournamentLocationCache
from outer_apis.scrapers.tennis.tournament_location import get_location, extract_city_candidate

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_USER_AGENT = "KairosTennisResearch/1.0 (internal research tool, contact: n/a)"
_REQUEST_TIMEOUT_S = 15
_PAUSE_BETWEEN_REQUESTS_S = 1.1  # margen real sobre el límite documentado de 1 req/s


def _geocode(city_candidate: str) -> tuple[float, float] | None:
    """Fail-closed: None si Nominatim no encuentra nada o la request
    falla -- nunca inventa coordenadas."""
    try:
        resp = requests.get(
            _NOMINATIM_URL,
            params={"q": city_candidate, "format": "json", "limit": 1},
            headers={"User-Agent": _USER_AGENT},
            timeout=_REQUEST_TIMEOUT_S,
        )
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    if not data:
        return None
    try:
        return float(data[0]["lat"]), float(data[0]["lon"])
    except (KeyError, ValueError, TypeError):
        return None


def collect_unresolved_city_candidates() -> dict[str, int]:
    """Candidatos de ciudad DISTINTOS (post-extracción) de todas las
    filas de `TennisMatchHistory` sin clima Y con torneo real que la
    tabla estática NO reconoce -- dict candidato -> cantidad de filas
    reales que se beneficiarían (para poder priorizar por volumen)."""
    init_db_historical()
    rows = TennisMatchHistory.select(TennisMatchHistory.tournament).where(
        (TennisMatchHistory.weather_fetched_at.is_null(True)) & (TennisMatchHistory.tournament.is_null(False))
    )
    candidates: dict[str, int] = {}
    for r in rows:
        if get_location(r.tournament) is not None:
            continue
        candidate = extract_city_candidate(r.tournament)
        if candidate is None:
            continue
        candidates[candidate] = candidates.get(candidate, 0) + 1
    return candidates


def run_geocoding(min_rows: int = 1) -> dict[str, int]:
    """Geocodifica cada candidato de ciudad DISTINTO que aún no esté en
    `TournamentLocationCache` (resuelto o no -- nunca reintenta lo ya
    intentado). `min_rows`: ignora candidatos con menos filas reales
    afectadas que este umbral (para priorizar volumen real primero en
    corridas parciales)."""
    init_db_historical()
    candidates = collect_unresolved_city_candidates()
    already_cached = {c.city_candidate for c in TournamentLocationCache.select(TournamentLocationCache.city_candidate)}

    to_geocode = sorted(
        (c for c, n in candidates.items() if n >= min_rows and c not in already_cached),
        key=lambda c: -candidates[c],
    )

    resolved = failed = 0
    for candidate in to_geocode:
        location = _geocode(candidate)
        time.sleep(_PAUSE_BETWEEN_REQUESTS_S)
        TournamentLocationCache.create(
            city_candidate=candidate,
            lat=location[0] if location else None,
            lon=location[1] if location else None,
            source="nominatim",
            fetched_at=datetime.now(timezone.utc),
        )
        if location:
            resolved += 1
        else:
            failed += 1

    return {"candidates_total": len(candidates), "geocoded_this_run": len(to_geocode), "resolved": resolved, "failed": failed}


if __name__ == "__main__":
    import sys

    min_rows = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    print(run_geocoding(min_rows=min_rows))
