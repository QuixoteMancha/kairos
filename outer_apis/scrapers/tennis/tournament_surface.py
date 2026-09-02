"""Mapeo estático torneo -> superficie (Clay/Hard/Grass), mismo patrón que
`outer_apis/scrapers/football/mls_teams.py` / `nfl_teams.py` (traducción
estática, sin adivinar nombres nuevos).

CONTEXTO (2026-07-30): Kalshi no expone superficie como campo estructurado
en ningún endpoint de mercados. El nombre del torneo SÍ está embebido en
`rules_secondary` (ej. "...match in the 2026 ATP Washington Round Of 16
after a ball has been played..."), confirmado contra la API real de
Kalshi. Este módulo traduce ese nombre de torneo a superficie.

Fail-closed: un torneo no reconocido devuelve None (nunca se adivina la
superficie de un torneo nuevo por analogía de nombre/país) -- el llamador
(`outer_apis/kalshi/markets/tennis.py`) debe tratar `surface=None` como
"desconocida", nunca como un valor por default.

Cobertura: torneos ATP/WTA de temporada 2026 con volumen real de mercados
en Kalshi al momento de este cambio (ver logs de producción 2026-07-30).
Lista deliberadamente incompleta -- se extiende cuando aparezca un torneo
nuevo sin traducción, no se rellena preventivamente con adivinanzas."""

from __future__ import annotations

import re

# Nombres tal como aparecen en rules_secondary de Kalshi (sin año/ronda).
_SURFACE_BY_TOURNAMENT: dict[str, str] = {
    # Hard court (temporada norteamericana de verano / indoor)
    "washington": "Hard",
    "memphis": "Hard",
    "los cabos": "Hard",
    "citi open": "Hard",
    "canadian open": "Hard",
    "cincinnati": "Hard",
    "us open": "Hard",
    "atlanta": "Hard",
    "winston-salem": "Hard",
    "winston salem": "Hard",
    "san diego": "Hard",
    "tokyo": "Hard",
    "shanghai": "Hard",
    "beijing": "Hard",
    "australian open": "Hard",
    "indian wells": "Hard",
    "miami open": "Hard",
    "miami": "Hard",
    "dubai": "Hard",
    "doha": "Hard",
    "acapulco": "Hard",
    # 2026-08-27: verificado contra histórico real (MatchHistory) antes de
    # agregar -- WTA Monterrey (308 partidos) y ATP Monterrey Challenger
    # (103 partidos) son unánimemente "hard", mismo valor para ambos tours.
    "monterrey": "Hard",
    "basel": "Hard",
    "vienna": "Hard",
    "paris masters": "Hard",
    "atp finals": "Hard",
    "wta finals": "Hard",
    # Clay
    "roland garros": "Clay",
    "french open": "Clay",
    "monte carlo": "Clay",
    "madrid": "Clay",
    "rome masters": "Clay",
    "rome": "Clay",
    "italian open": "Clay",
    "barcelona": "Clay",
    "hamburg": "Clay",
    "gstaad": "Clay",
    "bastad": "Clay",
    "båstad": "Clay",
    "kitzbuhel": "Clay",
    "kitzbühel": "Clay",
    "umag": "Clay",
    # Grass
    "wimbledon": "Grass",
    "queens club": "Grass",
    "queen's club": "Grass",
    "halle": "Grass",
    "eastbourne": "Grass",
    "s-hertogenbosch": "Grass",
    "s hertogenbosch": "Grass",
    "birmingham": "Grass",
    "mallorca": "Grass",
    "newport": "Grass",
}

_TOURNAMENT_RE = re.compile(
    r"in the \d{4} (?:ATP|WTA) (.+?) "
    r"(?:Round Of \d+|Quarterfinal|Semifinal|Final|Q\d+)",
    re.IGNORECASE,
)


def extract_tournament_name(rules_secondary: str | None) -> str | None:
    """Extrae el nombre del torneo de `rules_secondary` de Kalshi. Fail-closed:
    None si el texto no matchea el patrón esperado (nunca adivina)."""
    if not rules_secondary:
        return None
    m = _TOURNAMENT_RE.search(rules_secondary)
    return m.group(1).strip() if m else None


def get_surface(tournament_name: str | None) -> str | None:
    """Superficie ("Clay"/"Hard"/"Grass") para un nombre de torneo, o None
    si no está en la tabla estática -- nunca se adivina por país/nombre
    parecido."""
    if not tournament_name:
        return None
    return _SURFACE_BY_TOURNAMENT.get(tournament_name.strip().lower())


def surface_from_rules_secondary(rules_secondary: str | None) -> str | None:
    """Atajo: extrae el torneo y devuelve su superficie en un solo paso.
    Fail-closed en ambos pasos -- None si cualquiera de los dos falla."""
    return get_surface(extract_tournament_name(rules_secondary))
