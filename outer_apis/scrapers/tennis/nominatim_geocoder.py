"""Geocoder Nominatim (OpenStreetMap) -- ciudad -> (lat, lon, display).

2026-08-16, pedido explícito del usuario ("añade las mejores propuestas,
el geocode y el level"). RECONSTRUYE la pieza que faltaba: el geocoder
que poblaba `TournamentLocation` fue purgado en la consolidación de
2026-08-10 (ver `match_weather.py::_get_location_with_geocode_cache`),
y su ausencia es lo que hoy le pone TECHO REAL a la cobertura del worker
de clima -- sin `Tournament.lat/lon`, el clima depende de la tabla
estática de 73 sedes, o sea la cola larga (challengers/ITF en ciudades
arbitrarias) nunca tiene clima.

POLÍTICA DE USO REAL de Nominatim (https://operations.osmfoundation.org/
policies/nominatim/), respetada acá porque es un servicio gratuito y
comunitario:
  - Máximo 1 request por segundo ABSOLUTO -> `_MIN_INTERVAL_S = 1.1`,
    con lock para que sea seguro aunque lo llamen varios threads.
  - User-Agent identificable y real (NO uno de navegador falso).
  - Sin descargas masivas: los callers acotan sus tandas.

Fail-closed: cualquier fallo de red/parseo/resultado vacío devuelve
None -- nunca se inventa ni se aproxima una ubicación.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import requests

_BASE_URL = "https://nominatim.openstreetmap.org/search"
_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
_REQUEST_TIMEOUT_S = 20

# Identificación real, exigida por la política de uso de Nominatim.
_USER_AGENT = "Kairos-Tennis/1.0 (alertas@example.com)"

# Política dura: 1 req/s. Se usa 1.1s de margen.
_MIN_INTERVAL_S = 1.1
_throttle_lock = threading.Lock()
_last_request_at = 0.0


def _throttle() -> None:
    """Serializa y espacia las requests -- `time.sleep` DENTRO del lock a
    propósito: la política de Nominatim es 1 req/s global, no 1 req/s por
    thread, así que dos threads no pueden dormir en paralelo y disparar
    juntos (mismo criterio que el throttle de TennisExplorer)."""
    global _last_request_at
    with _throttle_lock:
        elapsed = time.monotonic() - _last_request_at
        if elapsed < _MIN_INTERVAL_S:
            time.sleep(_MIN_INTERVAL_S - elapsed)
        _last_request_at = time.monotonic()


def geocode_city(city: str) -> dict[str, Any] | None:
    """(lat, lon, display_name) reales de `city`, o None.

    Fail-closed en TODOS los casos dudosos:
      - respuesta vacía / status != 200 / JSON inválido -> None
      - coordenadas no parseables a float -> None
    Nunca devuelve un resultado "parecido": se pide `limit=1` y se toma
    el mejor match de OSM tal cual, o nada."""
    if not city or not city.strip():
        return None

    _throttle()
    try:
        resp = requests.get(
            _BASE_URL,
            params={"q": city.strip(), "format": "json", "limit": 1},
            headers={"User-Agent": _USER_AGENT},
            timeout=_REQUEST_TIMEOUT_S,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None

    if not isinstance(data, list) or not data:
        return None

    top = data[0]
    try:
        lat = float(top["lat"])
        lon = float(top["lon"])
    except (KeyError, TypeError, ValueError):
        return None

    return {
        "lat": lat,
        "lon": lon,
        "display_name": top.get("display_name") or city.strip(),
    }


def reverse_geocode(lat: float, lon: float) -> dict[str, Any] | None:
    """País real de una coordenada -- 2026-09-06, pedido explícito del
    usuario tras encontrar en vivo que `geocode_city` sin país (la
    query ambigua "solo ciudad") puede devolver un homónimo en OTRO
    CONTINENTE (confirmado: "Tigre challenger" -> Tigray, Etiopía en vez
    de Tigre, Argentina; "Toyota challenger" -> un punto de Arabia
    Saudita en vez de Toyota, Japón). Se usa para AUDITAR coordenadas ya
    guardadas contra el país real conocido de otra fuente (TennisExplorer,
    ver `get_tournament_info`) -- si no coinciden, la coordenada guardada
    está mal y debe re-geocodificarse con "ciudad, país".

    Returns: {"country", "display_name"}. Fail-closed: None ante
    cualquier fallo de red/parseo, o si la respuesta no trae país."""
    _throttle()
    try:
        resp = requests.get(
            _REVERSE_URL,
            # `accept-language=en` a propósito: sin esto, el país viene en
            # el idioma local (ej. "ኢትዮጵያ" en vez de "Ethiopia") --
            # confirmado en vivo -- lo que rompería cualquier comparación
            # de string contra el país real conocido de TennisExplorer.
            params={"lat": lat, "lon": lon, "format": "json", "accept-language": "en"},
            headers={"User-Agent": _USER_AGENT},
            timeout=_REQUEST_TIMEOUT_S,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None

    if not isinstance(data, dict):
        return None
    address = data.get("address") or {}
    country = address.get("country")
    if not country:
        return None
    return {
        "country": country,
        "country_code": (address.get("country_code") or "").upper() or None,
        "display_name": data.get("display_name") or "",
    }
