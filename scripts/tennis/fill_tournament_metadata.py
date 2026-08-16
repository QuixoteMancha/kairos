"""Relleno de `Tournament.level` y `Tournament.lat/lon/city` --
2026-08-16, pedido explícito del usuario ("añade las mejores propuestas,
el geocode y el level, descarga una lista que tenga las ciudades y los
levels de los torneos").

Ambos campos estaban al 0% en producción y NINGÚN componente los iba a
llenar (ver `kairos-reactive-gap-filler-worker-2026-08-16`).

## Nivel -- 3 fuentes, ninguna inventada, en orden de confianza

1. **El propio nombre del torneo** (fuente REAL, gratis, sin request):
   TennisExplorer YA codifica el nivel como sufijo del nombre --
   "Asuncion challenger", "Vitoria-Gasteiz ITF" (verificado contra la
   base real de producción). Esto resuelve la distinción MÁS importante
   para los backtests del proyecto -- "250+" siempre significó
   "circuito principal, ni challenger ni ITF" -- sin pedirle nada a
   nadie.
2. **Tabla estática de niveles altos** (`_MAIN_TOUR_LEVELS`): Grand
   Slam, Masters 1000 y Finals, estables año a año. Es un atajo sin
   request, no la fuente de verdad (ver punto 3).
3. **Página de torneo de TennisExplorer** (`refine_generic_levels` ->
   `tennisexplorer_tournament_scraper`): nivel EXACTO a partir de los
   puntos de ranking del ganador (2000 = Grand Slam, 1000 = Masters
   1000, 500, 250...), + ciudad y país reales.

   CORRECCIÓN de una conclusión previa mía (2026-08-16, a raíz de la
   pregunta del usuario "¿tennis explorer no trae el nombre del
   torneo?"): yo había afirmado que separar 500 de 250 era imposible
   sin una fuente nueva y lo había dejado en un genérico "ATP Tour".
   Era FALSO -- el dato estaba en TennisExplorer todo el tiempo. El
   genérico ahora es solo un estado TRANSITORIO: lo pone el paso 1/2
   (sin red) y el paso 3 lo refina al nivel exacto.
4. **API oficial WTA** (`wta_tournament_level_backfill.py`, ya
   existente): niveles reales del lado WTA. Se mantiene aparte, este
   módulo no la duplica.

La API oficial de ATP se probó en vivo (2026-08-16) y está BLOQUEADA
(403/`itp-api` sin ruta, `protennislive` 401), y el repo de Sackmann que
la suplía ya no existe (404) -- por eso el nivel exacto sale de la
página de TennisExplorer y no de una descarga oficial.

NUNCA DEGRADA: igual que `wta_tournament_level_backfill`, si la fila ya
tiene un nivel de mayor jerarquía, se conserva.

## Ubicación -- Nominatim (OpenStreetMap)

Reusa lo que ya existía y estaba bien pensado:
`extract_city_candidate` (saca la ciudad del string crudo del torneo) +
`_BLOCKLIST_KEYWORDS` (eventos de equipo/liga como Davis Cup o
Bundesliga NO tienen sede única -- nunca se les asigna ciudad).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from database.db import init_db_historical
from database.historical.tennis.models import Tournament
from outer_apis.scrapers.tennis.nominatim_geocoder import geocode_city
from outer_apis.scrapers.tennis.tennisexplorer_tournament_scraper import get_tournament_info
from outer_apis.scrapers.tennis.tournament_location import (
    extract_city_candidate, get_location, normalize_tournament_name,
)

# Jerarquía compartida con `wta_tournament_level_backfill._LEVEL_PRIORITY`
# (mismos nombres de nivel) + los del lado ATP y los genéricos de
# circuito principal. Sirve para no degradar nunca un nivel ya conocido.
_LEVEL_PRIORITY = {
    "Grand Slam": 6,
    "WTA 1000": 5, "Premier Mandatory": 5, "Premier 5": 5, "Masters 1000": 5,
    "WTA 500": 4, "Premier": 4, "Finals": 4, "ATP 500": 4,
    "WTA 250": 3, "International": 3, "ATP 250": 3,
    # Circuito principal con nivel exacto DESCONOCIDO -- por encima de
    # challenger/ITF (que es la distinción que importa para el filtro
    # "250+"), por debajo de cualquier nivel concreto ya confirmado.
    "ATP Tour": 2.5, "WTA Tour": 2.5,
    "WTA 125": 2, "125K": 2, "Challenger": 2,
    "ITF": 1,
}

# Solo niveles ESTABLES y verificables (ver docstring). Claves ya
# normalizadas (`normalize_tournament_name`: minúsculas, sin acentos,
# sin sufijos de tour/nivel).
_MAIN_TOUR_LEVELS = {
    # --- Grand Slam ---
    "australian open": "Grand Slam", "melbourne": "Grand Slam",
    "roland garros": "Grand Slam", "french open": "Grand Slam",
    "wimbledon": "Grand Slam",
    "us open": "Grand Slam",
    # --- Masters 1000 / WTA 1000 (sedes estables hace años) ---
    "indian wells": "Masters 1000", "miami": "Masters 1000",
    "monte carlo": "Masters 1000", "montecarlo": "Masters 1000",
    "madrid": "Masters 1000", "rome": "Masters 1000", "roma": "Masters 1000",
    "toronto": "Masters 1000", "montreal": "Masters 1000",
    "cincinnati": "Masters 1000", "shanghai": "Masters 1000",
    # --- Finales de temporada ---
    "atp finals": "Finals", "wta finals": "Finals",
    "masters cup": "Finals", "next gen atp finals": "Finals",
}

# Sufijos que TennisExplorer pega al nombre y que SON el nivel real.
_LEVEL_BY_NAME_KEYWORD = (
    ("challenger", "Challenger"),
    ("itf", "ITF"),
)


def _priority(level: str | None) -> float:
    return _LEVEL_PRIORITY.get(level, 0)


def derive_tournament_level(tournament_name: str, tour: str | None = None) -> str | None:
    """Nivel REAL derivable de un nombre de torneo, sin ninguna request.

    Orden: sufijo challenger/ITF (dato explícito de la fuente) -> tabla
    estática de niveles altos -> genérico de circuito principal según
    `tour`. Fail-closed: None si no se puede afirmar nada (ej. evento de
    equipo, o `tour` desconocido) -- nunca se adivina un nivel."""
    if not tournament_name or not tournament_name.strip():
        return None

    lowered = tournament_name.strip().lower()
    for keyword, level in _LEVEL_BY_NAME_KEYWORD:
        # Se busca como TOKEN, no como substring: evita que "Itf" dentro
        # de otra palabra (o una ciudad que contenga "challenger") den
        # un falso positivo.
        if keyword in lowered.replace("-", " ").split():
            return level

    normalized = normalize_tournament_name(tournament_name)
    static_level = _MAIN_TOUR_LEVELS.get(normalized)
    if static_level is not None:
        return static_level

    # Circuito principal, nivel exacto desconocido -- honesto y
    # suficiente para el filtro "250+". Los eventos de equipo/liga
    # quedan fuera vía la blocklist de `extract_city_candidate`.
    if extract_city_candidate(tournament_name) is None:
        return None
    if tour in ("ATP", "WTA"):
        return f"{tour} Tour"
    return None


def fill_tournament_levels(limit: int | None = None) -> dict[str, Any]:
    """Completa `Tournament.level` donde falte, sin red.

    Nunca degrada: si la fila ya tiene un nivel de mayor jerarquía que
    el derivado, se conserva el existente (mismo criterio que el
    backfill oficial WTA)."""
    init_db_historical()

    query = Tournament.select().where(Tournament.level.is_null())
    if limit is not None:
        query = query.limit(limit)

    updated = skipped = 0
    now = datetime.now(timezone.utc)
    for tournament in query:
        level = derive_tournament_level(tournament.tournament_name, tournament.tour)
        if level is None or _priority(level) <= _priority(tournament.level):
            skipped += 1
            continue
        tournament.level = level
        tournament.fetched_at = now
        tournament.save()
        updated += 1

    return {"updated": updated, "skipped": skipped}


def refine_generic_levels(limit: int = 30) -> dict[str, Any]:
    """Reemplaza los niveles GENÉRICOS ("ATP Tour"/"WTA Tour") por el
    nivel EXACTO (Grand Slam / Masters 1000 / 500 / 250) leído de la
    página real del torneo en TennisExplorer.

    2026-08-16: nace de la pregunta del usuario "¿tennis explorer no
    trae el nombre del torneo?" -- resultó que trae ciudad, país Y los
    puntos de ranking del ganador, que SON el nivel. Esto corrige mi
    conclusión previa de que 500-vs-250 era indeterminable.

    De paso guarda la ciudad y el país reales, que es lo que después
    hace preciso al geocoding (ver `fill_tournament_locations`).

    Solo toca filas con nivel genérico: los challenger/ITF ya tienen su
    nivel exacto derivado del nombre sin gastar un request, y los que ya
    tienen nivel concreto no se re-consultan."""
    init_db_historical()

    query = (
        Tournament
        .select()
        .where(Tournament.level.in_(["ATP Tour", "WTA Tour"]))
        .limit(limit)
    )

    processed = refined = not_found = 0
    now = datetime.now(timezone.utc)
    for tournament in query:
        processed += 1
        info = get_tournament_info(
            tournament.tournament_name, tournament.year, tournament.tour,
        )
        if info is None:
            not_found += 1
            continue

        changed = False
        if info["level"] and _priority(info["level"]) > _priority(tournament.level):
            tournament.level = info["level"]
            changed = True
        # Ciudad/país reales -- alimentan el geocoding posterior.
        if info["city"] and not tournament.city:
            tournament.city = info["city"]
            changed = True
        if info["country"] and not tournament.country:
            tournament.country = info["country"]
            changed = True

        if changed:
            tournament.fetched_at = now
            tournament.save()
            refined += 1

    return {"processed": processed, "refined": refined, "not_found": not_found}


def fill_tournament_locations(limit: int = 25) -> dict[str, Any]:
    """Completa `Tournament.lat`/`lon`/`city` donde falten.

    Prioriza la tabla estática ya verificada (`get_location`, sin
    request) y solo cae a Nominatim para lo que no está ahí -- la cola
    larga real (challengers/ITF en ciudades arbitrarias), que es
    justamente lo que hoy deja al worker de clima sin cobertura.

    `limit` es bajo a propósito: Nominatim permite 1 request por segundo
    y es un servicio comunitario gratuito. El worker corre cada hora, así
    que se completa de a tandas.

    Fail-closed: si no se puede extraer una ciudad candidata (ej. evento
    de equipo/liga, ver `_BLOCKLIST_KEYWORDS`) o Nominatim no devuelve
    nada, la fila se saltea SIN escribir -- nunca una sede aproximada."""
    init_db_historical()

    query = Tournament.select().where(Tournament.lat.is_null() | Tournament.lon.is_null())

    processed = updated = no_city = not_found = 0
    now = datetime.now(timezone.utc)
    for tournament in query:
        if processed >= limit:
            break

        city = extract_city_candidate(tournament.tournament_name)
        if city is None:
            no_city += 1
            continue

        processed += 1

        static = get_location(tournament.tournament_name)
        if static is not None:
            lat, lon = static
            display_city = city
        else:
            # 2026-08-16: si ya conocemos ciudad+país reales (leídos de
            # la página de torneo de TennisExplorer, ver
            # `refine_generic_levels`), se consulta "Ciudad, País" en vez
            # de solo la ciudad -- desambigua casos reales como
            # "Santiago" (Chile / Compostela / Cuba).
            if tournament.city and tournament.country:
                query = f"{tournament.city}, {tournament.country}"
            else:
                query = city
            geo = geocode_city(query)
            if geo is None:
                not_found += 1
                continue
            lat, lon = geo["lat"], geo["lon"]
            display_city = tournament.city or city

        tournament.lat = lat
        tournament.lon = lon
        if not tournament.city:
            tournament.city = display_city
        tournament.fetched_at = now
        tournament.save()
        updated += 1

    return {
        "processed": processed, "updated": updated,
        "sin_ciudad_extraible": no_city, "no_encontrados": not_found,
    }
