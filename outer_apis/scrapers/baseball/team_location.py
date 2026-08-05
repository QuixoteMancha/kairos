"""Coordenadas reales (lat/lon) del estadio local de cada equipo de
MLB -- 2026-08-05, pedido explícito del usuario ("adapta el backfill
de clima de tenis para los otros deportes que tiene el sistema").

A diferencia de tenis (sede variable por torneo, tabla enorme y
deliberadamente incompleta), en deportes de equipo la ubicación es
mucho más simple: cada partido se juega en el estadio del equipo
LOCAL (`home_team`), y ese estadio es fijo -- 30 equipos reales en
MLB, tabla completa desde el día 1, sin cola larga que geocodificar.

Fail-closed: un equipo no reconocido devuelve None -- nunca se inventa
una ubicación aproximada (mismo criterio que `tournament_location.py`)."""

from __future__ import annotations

# (lat, lon) del estadio real de cada equipo -- clave = nombre completo
# tal como lo entrega statsapi.mlb.com (`teams.home.team.name`).
_LOCATION_BY_TEAM: dict[str, tuple[float, float]] = {
    "Arizona Diamondbacks": (33.4455, -112.0667),
    "Atlanta Braves": (33.8907, -84.4677),
    "Baltimore Orioles": (39.2838, -76.6217),
    "Boston Red Sox": (42.3467, -71.0972),
    "Chicago Cubs": (41.9484, -87.6553),
    "Chicago White Sox": (41.8300, -87.6338),
    "Cincinnati Reds": (39.0975, -84.5071),
    "Cleveland Guardians": (41.4962, -81.6852),
    "Colorado Rockies": (39.7559, -104.9942),
    "Detroit Tigers": (42.3390, -83.0485),
    "Houston Astros": (29.7573, -95.3555),
    "Kansas City Royals": (39.0517, -94.4803),
    "Los Angeles Angels": (33.8003, -117.8827),
    "Los Angeles Dodgers": (34.0739, -118.2400),
    "Miami Marlins": (25.7781, -80.2196),
    "Milwaukee Brewers": (43.0280, -87.9712),
    "Minnesota Twins": (44.9817, -93.2777),
    "New York Mets": (40.7571, -73.8458),
    "New York Yankees": (40.8296, -73.9262),
    "Oakland Athletics": (37.7516, -122.2005),
    "Athletics": (37.7516, -122.2005),  # rebrand real (mudanza Oakland->Sacramento en curso, ver nota abajo)
    "Philadelphia Phillies": (39.9061, -75.1665),
    "Pittsburgh Pirates": (40.4469, -80.0057),
    "San Diego Padres": (32.7073, -117.1566),
    "San Francisco Giants": (37.7786, -122.3893),
    "Seattle Mariners": (47.5914, -122.3325),
    "St. Louis Cardinals": (38.6226, -90.1928),
    "Tampa Bay Rays": (27.7683, -82.6534),
    "Texas Rangers": (32.7473, -97.0842),
    "Toronto Blue Jays": (43.6414, -79.3894),
    "Washington Nationals": (38.8730, -77.0074),
}


def get_team_location(team_name: str | None) -> tuple[float, float] | None:
    """(lat, lon) del estadio real de `team_name`, o None si no está en
    la tabla -- nunca se adivina."""
    if not team_name:
        return None
    return _LOCATION_BY_TEAM.get(team_name.strip())
