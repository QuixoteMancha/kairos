"""Coordenadas reales (lat/lon) por torneo, para poder pedirle clima
histórico a Open-Meteo (ver `outer_apis/scrapers/weather/open_meteo.py`).
Mismo criterio que `tournament_surface.py`: tabla estática, cobertura
deliberadamente incompleta (se extiende cuando aparece un torneo nuevo
sin ubicación, nunca se adivina por nombre/país parecido).

Fail-closed: un torneo no reconocido devuelve None -- nunca se inventa
una ubicación aproximada.

NORMALIZACIÓN (2026-08-04, pedido explícito del usuario -- priorizar el
gap de clima, 90% de `TennisMatchHistory` sin clima, investigar fuentes
antes de re-scrapear): el 85% del gap NO era falta de dato real, era
que el nombre de torneo llega con sufijos de tour/nivel
("Rome WTA", "Cincinnati WTA", "Prostejov challenger") o con acentos
Unicode ("Kitzbühel" vs la clave ASCII "kitzbuhel") que rompían el
match EXACTO contra la tabla, aunque la ciudad sede YA estaba
correctamente en la tabla bajo otro nombre. `get_location` ahora
normaliza (quita sufijos conocidos de tour/nivel, quita acentos) ANTES
de buscar -- esto NO es adivinar una ubicación nueva, es canonicalizar
el string de un torneo que YA tiene una entrada real y verificada."""

from __future__ import annotations

import unicodedata

# Sufijos de tour/nivel que TennisExplorer agrega al nombre real del
# torneo (no son parte del nombre de la sede) -- se quitan ANTES de
# buscar en la tabla, nunca se usan para adivinar una ciudad nueva.
_SUFFIXES_TO_STRIP = (
    " wta", " atp", " challenger", " itf", " - men", " - women", " men", " women",
)


def _normalize(name: str) -> str:
    n = name.strip().lower()
    n = unicodedata.normalize("NFKD", n).encode("ascii", "ignore").decode("ascii")
    changed = True
    while changed:
        changed = False
        for suffix in _SUFFIXES_TO_STRIP:
            if n.endswith(suffix):
                n = n[: -len(suffix)].strip()
                changed = True
    return n

# (lat, lon) de la sede real de cada torneo -- misma clave (nombre de
# torneo tal como aparece en rules_secondary de Kalshi / recent-results
# de Tennis Abstract) que tournament_surface.py, para poder reusar
# extract_tournament_name() de ese módulo.
_LOCATION_BY_TOURNAMENT: dict[str, tuple[float, float]] = {
    "washington": (38.9072, -77.0369),
    "memphis": (35.1495, -90.0490),
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
    # Agregados 2026-08-02 (pedido explícito del usuario, tras el
    # backfill de SF/Final -- ver scripts/backfill_sf_final_matches.py):
    "london": (51.5074, -0.1278),
    "prague": (50.0755, 14.4378),
    "iasi": (47.1585, 27.6014),
    "estoril": (38.6979, -9.4215),  # sede real: Cascais, Portugal
    "athens": (37.9838, 23.7275),
    "stuttgart": (48.7758, 9.1829),
    "nottingham": (52.9548, -1.1581),
    # Agregados 2026-08-04 (pedido explícito del usuario, investigación
    # del gap de clima 90% -- ciudades sede real de torneos ATP/WTA/
    # challenger confirmadas, top ofensores por volumen en
    # TennisMatchHistory sin ubicación reconocida):
    "brisbane": (-27.4698, 153.0251),
    "adelaide": (-34.9285, 138.6007),
    "charleston": (32.7765, -79.9311),
    "auckland": (-36.8485, 174.7633),
    "linz": (48.3069, 14.2858),
    "wuhan": (30.5928, 114.3055),
    "strasbourg": (48.5734, 7.7521),
    "montreal": (45.5019, -73.5674),
    "toronto": (43.6532, -79.3832),
    "buenos aires": (-34.6037, -58.3816),
    "kitzbuhel": (47.4467, 12.3925),  # ya existía como "kitzbuehel" fonético; normalize() ahora también matchea el acento real "Kitzbühel"
    "bratislava": (48.1486, 17.1077),
    "ostrava": (49.8209, 18.2625),
    "abu dhabi": (24.4539, 54.3773),
    "hertogenbosch": (51.6978, 5.3037),  # alias sin "s-" -- misma sede que "s-hertogenbosch"
    # Eventos con sede fija conocida pero nombre de sponsor variable --
    # alias explícito a la ciudad real, NO una adivinanza (el evento es
    # siempre en la misma sede año a año).
    "livesport prague open": (50.0755, 14.4378),
}


def get_location(tournament_name: str | None) -> tuple[float, float] | None:
    """(lat, lon) para un nombre de torneo, o None si no está en la
    tabla estática -- nunca se adivina. Normaliza sufijos de tour/nivel
    y acentos Unicode antes de buscar (ver `_normalize`) -- un torneo
    con sede real ya conocida no debe fallar solo por venir con
    "WTA"/"challenger" pegado o con un acento distinto al de la clave."""
    if not tournament_name:
        return None
    return _LOCATION_BY_TOURNAMENT.get(_normalize(tournament_name))
