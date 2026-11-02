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

Una precisión importante: Nominatim no siempre representa una ciudad con
``class=place``. En OSM, por ejemplo, Brescia y L'Aquila se devuelven como
``boundary/administrative``. Por eso NO basta con rechazar todo boundary:
se acepta únicamente un límite administrativo de granularidad local cuyo
``address`` identifica exactamente la ciudad buscada. Se rechazan regiones,
condados, picos, parques y cualquier resultado sin una localidad verificable.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any
import unicodedata

import requests

_BASE_URL = "https://nominatim.openstreetmap.org/search"
_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
_REQUEST_TIMEOUT_S = 20

# Identificación real, exigida por la política de uso de Nominatim
# (https://operations.osmfoundation.org/policies/nominatim/): un contacto
# válido, configurable por entorno.
_USER_AGENT = f"Kairos-Tennis/1.0 ({os.getenv('NOMINATIM_CONTACT', 'configure-NOMINATIM_CONTACT')})"

# Política dura: 1 req/s. Se usa 1.1s de margen.
_MIN_INTERVAL_S = 1.1
_throttle_lock = threading.Lock()
_last_request_at = 0.0

_SETTLEMENT_TYPES = {"city", "town", "village", "hamlet"}
_LOCALITY_ADDRESS_KEYS = ("city", "town", "village", "hamlet", "municipality")

# TennisExplorer no usa siempre el nombre ISO ("USA", "RSA", "Chinese
# Taipei", "Bosnia and Herzeg.", etc.). Nominatim sí expone ISO-3166-1 en
# ``address.country_code``: comparar ambos evita que una búsqueda estructurada
# que no entiende un alias devuelva una ciudad homónima de OTRO país. La tabla
# cubre todos los países actualmente presentes en Tournament; una clave que no
# figure se rechaza, no se adivina.
_COUNTRY_CODES = {
    "algeria": "dz", "argentina": "ar", "armenia": "am", "australia": "au",
    "austria": "at", "azerbaijan": "az", "bahrain": "bh", "belarus": "by",
    "belgium": "be", "bolivia": "bo", "bosniaandherzeg": "ba", "botswana": "bw",
    "brazil": "br", "bulgaria": "bg", "burundi": "bi", "canada": "ca",
    "chile": "cl", "china": "cn", "chinesetaipei": "tw", "colombia": "co",
    "croatia": "hr", "cyprus": "cy", "czechrepublic": "cz", "denmark": "dk",
    "djibouti": "dj", "dominicanrep": "do", "ecuador": "ec", "egypt": "eg",
    "elsalvador": "sv", "england": "gb", "estonia": "ee", "finland": "fi",
    "france": "fr", "georgia": "ge", "germany": "de", "greatbritain": "gb",
    "greece": "gr", "guatemala": "gt", "hongkong": "hk", "hungary": "hu",
    "india": "in", "indonesia": "id", "ireland": "ie", "israel": "il",
    "italy": "it", "ivorycoast": "ci", "jamaica": "jm", "japan": "jp",
    "kazakhstan": "kz", "kenya": "ke", "korea": "kr", "latvia": "lv",
    "lebanon": "lb", "lithuania": "lt", "luxembourg": "lu", "macau": "mo",
    "malaysia": "my", "malta": "mt", "mauritius": "mu", "mexico": "mx",
    "moldavsko": "md", "monaco": "mc", "montenegro": "me", "morocco": "ma",
    "netherlands": "nl", "newcaledonia": "nc", "newzealand": "nz", "nigeria": "ng",
    "northmacedonia": "mk", "norway": "no", "pakistan": "pk", "paraguay": "py",
    "peru": "pe", "philippines": "ph", "poland": "pl", "portugal": "pt",
    "qatar": "qa", "rsa": "za", "romania": "ro", "russia": "ru", "rwanda": "rw",
    "sanmarino": "sm", "saudiarabia": "sa", "scotland": "gb", "senegal": "sn",
    "serbia": "rs", "singapore": "sg", "slovakia": "sk", "slovenia": "si",
    "southkorea": "kr", "spain": "es", "srilanka": "lk", "sweden": "se",
    "switzerland": "ch", "thailand": "th", "tunisia": "tn", "turkey": "tr",
    "uae": "ae", "usa": "us", "ukraine": "ua", "uruguay": "uy", "uzbekistan": "uz",
}


def _normalized_name(value: str) -> str:
    """Comparación conservadora de nombres de ciudad (acentos/caso/puntuación).

    No intenta resolver alias ni nombres "parecidos": para confiar en un
    resultado de Nominatim la localidad declarada debe ser la misma ciudad
    solicitada, no solo estar cerca o compartir país.
    """
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(char for char in decomposed.lower() if char.isalnum())


def _result_category(result: dict[str, Any]) -> str:
    # JSON clásico usa ``class``; JSONv2 lo renombra a ``category``.
    return str(result.get("class") or result.get("category") or "").lower()


def _locality_matches(result: dict[str, Any], city: str) -> bool:
    address = result.get("address")
    if not isinstance(address, dict):
        return False
    wanted = _normalized_name(city)
    return bool(wanted) and any(
        _normalized_name(str(address[key])) == wanted
        for key in _LOCALITY_ADDRESS_KEYS
        if address.get(key)
    )


def _country_matches(result: dict[str, Any], country: str | None) -> bool:
    """Comprueba el ISO de Nominatim cuando el caller conoce el país."""
    if not country or not country.strip():
        return True
    expected = _COUNTRY_CODES.get(_normalized_name(country))
    address = result.get("address")
    actual = address.get("country_code") if isinstance(address, dict) else None
    return bool(expected and actual and str(actual).lower() == expected)


def _settlement_priority(result: dict[str, Any], city: str, country: str | None) -> int | None:
    """Devuelve prioridad de un resultado apto, o None si es inseguro.

    Un ``boundary/administrative`` sólo se permite cuando Nominatim lo marca
    como localidad concreta (``place_rank >= 16`` y address.city/town/...)
    igual a la consulta. La granularidad de la localidad manda sobre la
    clase: un ``boundary`` de *city* (Austin, Texas) es más preciso que un
    ``place`` de *village* homónimo (Austin, Nevada). A igual localidad,
    ``place`` gana sobre ``boundary``. Esto excluye los centroides de
    provincia/condado que causaron las elevaciones absurdas de Brescia,
    Ourense y L'Aquila.
    """
    category = _result_category(result)
    result_type = str(result.get("type") or "").lower()
    if not _locality_matches(result, city) or not _country_matches(result, country):
        return None
    address = result.get("address") or {}
    locality_rank = {
        "city": 40, "town": 30, "village": 20, "hamlet": 10, "municipality": 5,
    }
    matching_locality = next(
        (key for key in _LOCALITY_ADDRESS_KEYS
         if address.get(key) and _normalized_name(str(address[key])) == _normalized_name(city)),
        None,
    )
    if matching_locality is None:
        return None
    if category == "place" and result_type in _SETTLEMENT_TYPES:
        return locality_rank[matching_locality] + 1
    if category == "boundary" and result_type == "administrative":
        try:
            place_rank = int(result.get("place_rank"))
        except (TypeError, ValueError):
            return None
        if place_rank >= 16:
            return locality_rank[matching_locality]
    return None


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


def geocode_city(city: str, country: str | None = None) -> dict[str, Any] | None:
    """Coordenadas de un asentamiento real, o ``None``.

    Fail-closed en TODOS los casos dudosos:
      - respuesta vacía / status != 200 / JSON inválido -> None
      - coordenadas no parseables a float -> None
      - pico/parque/región/condado, o ciudad de nombre distinto -> None

    Cuando se conoce ``country`` forma la búsqueda libre ``"Ciudad, País"``.
    Es preferible a los campos estructurados de Nominatim para homónimos
    dentro de un país (``Austin, USA`` debe priorizar Texas, no un pequeño
    Austin de Nevada); ``address.country_code`` se valida además como
    defensa independiente. Se pide más de un resultado: el primero puede ser
    una provincia o una montaña homónima aunque una ciudad válida exista más
    abajo.
    """
    if not city or not city.strip():
        return None

    city = city.strip()
    params: dict[str, Any] = {
        "format": "json",
        "limit": 10,
        "addressdetails": 1,
    }
    params["q"] = f"{city}, {country.strip()}" if country and country.strip() else city

    _throttle()
    try:
        resp = requests.get(
            _BASE_URL,
            params=params,
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

    candidates: list[tuple[int, int, dict[str, Any]]] = []
    for index, result in enumerate(data):
        if not isinstance(result, dict):
            continue
        priority = _settlement_priority(result, city, country)
        if priority is not None:
            candidates.append((priority, index, result))
    if not candidates:
        return None

    # Prioriza el tipo de localidad (city > town > village); a igualdad de
    # localidad, ``place`` sobre boundary y luego el ranking de Nominatim.
    _priority, index, top = max(candidates, key=lambda item: (item[0], -item[1]))
    try:
        lat = float(top["lat"])
        lon = float(top["lon"])
    except (KeyError, TypeError, ValueError):
        return None

    return {
        "lat": lat,
        "lon": lon,
        "display_name": top.get("display_name") or city,
        # Se preserva evidencia para auditorías; callers existentes sólo
        # consumen lat/lon/display_name y siguen siendo compatibles.
        "class": _result_category(top),
        "type": top.get("type"),
        "importance": top.get("importance"),
        "place_rank": top.get("place_rank"),
        "address": top.get("address") or {},
        "candidate_rank": index + 1,
    }


def reverse_geocode(lat: float, lon: float) -> dict[str, Any] | None:
    """Metadatos geográficos reales de una coordenada, o ``None``.

    Además del país, conserva ``address`` y la clasificación OSM del objeto
    inverso.  Las auditorías de asentamientos usan esos campos para probar que
    una coordenada ya guardada *no* pertenece a la ciudad del torneo antes de
    resetearla; una mera diferencia contra otro resultado de búsqueda no es
    evidencia suficiente (ciudades homónimas dentro del mismo país existen).

    Contexto original -- 2026-09-06, pedido explícito del
    usuario tras encontrar en vivo que `geocode_city` sin país (la
    query ambigua "solo ciudad") puede devolver un homónimo en OTRO
    CONTINENTE (confirmado: "Tigre challenger" -> Tigray, Etiopía en vez
    de Tigre, Argentina; "Toyota challenger" -> un punto de Arabia
    Saudita en vez de Toyota, Japón). Se usa para AUDITAR coordenadas ya
    guardadas contra el país real conocido de otra fuente (TennisExplorer,
    ver `get_tournament_info`) -- si no coinciden, la coordenada guardada
    está mal y debe re-geocodificarse con "ciudad, país".

    Returns: {"country", "display_name", "address", "class", ...}.
    Fail-closed: None ante
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
        "address": address,
        "class": _result_category(data),
        "type": data.get("type"),
        "place_rank": data.get("place_rank"),
    }
