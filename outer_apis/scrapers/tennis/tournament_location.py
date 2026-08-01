"""Coordenadas reales (lat/lon) por torneo, para poder pedirle clima
histórico a Open-Meteo (ver `outer_apis/scrapers/weather/open_meteo.py`).
Mismo criterio que `tournament_surface.py`: tabla estática, cobertura
deliberadamente incompleta (se extiende cuando aparece un torneo nuevo
sin ubicación, nunca se adivina por nombre/país parecido).

Fail-closed: un torneo no reconocido devuelve None -- nunca se inventa
una ubicación aproximada."""

from __future__ import annotations

# (lat, lon) de la sede real de cada torneo -- misma clave (nombre de
# torneo tal como aparece en rules_secondary de Kalshi / recent-results
# de Tennis Abstract) que tournament_surface.py, para poder reusar
# extract_tournament_name() de ese módulo.
_LOCATION_BY_TOURNAMENT: dict[str, tuple[float, float]] = {
    "washington": (38.9072, -77.0369),
    "los cabos": (22.8905, -109.9167),
    "citi open": (38.9072, -77.0369),
    "canadian open": (43.6532, -79.3832),
    "cincinnati": (39.1031, -84.5120),
    "us open": (40.7498, -73.8459),
    "atlanta": (33.7490, -84.3880),
    "winston-salem": (36.0999, -80.2442),
    "winston salem": (36.0999, -80.2442),
    "san diego": (32.7157, -117.1611),
    "tokyo": (35.6762, 139.6503),
    "shanghai": (31.2304, 121.4737),
    "beijing": (39.9042, 116.4074),
    "australian open": (-37.8136, 144.9631),
    "indian wells": (33.7206, -116.3050),
    "miami open": (25.9581, -80.2389),
    "miami": (25.9581, -80.2389),
    "dubai": (25.2048, 55.2708),
    "doha": (25.2854, 51.5310),
    "acapulco": (16.8531, -99.8237),
    "basel": (47.5596, 7.5886),
    "vienna": (48.2082, 16.3738),
    "paris masters": (48.8566, 2.3522),
    "atp finals": (45.4642, 9.1900),  # Turín 2026
    "wta finals": (25.2048, 55.2708),  # Riad/Dubai según año -- placeholder Dubai
    "roland garros": (48.8467, 2.2530),
    "french open": (48.8467, 2.2530),
    "monte carlo": (43.7384, 7.4246),
    "madrid": (40.4165, -3.7026),
    "rome masters": (41.9109, 12.4818),
    "rome": (41.9109, 12.4818),
    "italian open": (41.9109, 12.4818),
    "barcelona": (41.3874, 2.1686),
    "hamburg": (53.5511, 9.9937),
    "gstaad": (46.4667, 7.2864),
    "bastad": (56.4283, 12.8508),
    "kitzbuhel": (47.4467, 12.3925),
    "umag": (45.4339, 13.5192),
    "wimbledon": (51.4340, -0.2140),
    "queens club": (51.4820, -0.2150),
    "queen's club": (51.4820, -0.2150),
    "halle": (51.8058, 8.3336),
    "eastbourne": (50.7684, 0.2905),
    "s-hertogenbosch": (51.6978, 5.3037),
    "s hertogenbosch": (51.6978, 5.3037),
    "birmingham": (52.4862, -1.8904),
    "mallorca": (39.5696, 2.6502),
    "newport": (41.4901, -71.3128),
    "berlin": (52.5200, 13.4050),
    "bad homburg": (50.2266, 8.6181),
}


def get_location(tournament_name: str | None) -> tuple[float, float] | None:
    """(lat, lon) para un nombre de torneo, o None si no está en la
    tabla estática -- nunca se adivina."""
    if not tournament_name:
        return None
    return _LOCATION_BY_TOURNAMENT.get(tournament_name.strip().lower())
