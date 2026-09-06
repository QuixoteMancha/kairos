"""Auditoría + corrección de `Tournament.lat`/`lon` geocodificados sin
país conocido -- 2026-09-06, bug real encontrado en vivo investigando el
veto de fatiga+clima: 8,502 de 8,600 torneos ya geocodificados (98.9%)
pasaron por la query ambigua "solo ciudad" de Nominatim (sin país para
desambiguar), porque `refine_generic_levels` solo completaba `country`
para torneos ATP/WTA Tour -- ver `fill_tournament_country` (fix del
gate) para el resto de la investigación.

Confirmado en vivo: "Tigre challenger" (Argentina real) quedó
geocodificado en Tigray, Etiopía; "Toyota challenger" (Japón real) en un
punto de Arabia Saudita -- homónimos en otro continente ganándole a
Nominatim sin país para filtrar.

MÉTODO: para cada torneo con `lat`/`lon` pero sin `country` conocido,
1. Pide el país REAL a TennisExplorer (`get_tournament_info`, misma
   fuente de verdad que ya usa `fill_tournament_country`).
2. Reverse-geocodifica la coordenada YA GUARDADA (cacheada por
   coordenada redondeada a 3 decimales -- muchos torneos comparten
   sede, no hace falta repetir el reverse-geocode para cada uno).
3. Si el país real y el país de la coordenada guardada NO coinciden
   (comparación normalizada, ver `_normalize`), la coordenada está mal:
   se resetea `lat`/`lon`/`elevation_m` a NULL (deja que
   `fill_tournament_locations` la vuelva a resolver bien, ahora SÍ con
   país conocido) y se guarda el país real.
4. Si coinciden, se guarda igual el país real (queda documentado, no
   hace falta volver a auditar esta fila) y se deja la coordenada como
   está.

Fail-closed: si TennisExplorer no responde para un torneo, se lo salta
sin tocar nada (nunca se resetea una coordenada sin saber si está mal).
Mismo `take_rotating_batch` anti-starvation que el resto de los
enrichments de este paquete.

Uso: `python -m scripts.tennis.audit_tournament_geocoding [--limit N]`."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from database.db import db_historical, init_db_historical
from database.historical.tennis.models import Tournament
from outer_apis.scrapers.tennis.nominatim_geocoder import reverse_geocode
from outer_apis.scrapers.tennis.tennisexplorer_tournament_scraper import get_tournament_info
from scripts.tennis.rotating_batch import take_rotating_batch

_DEFAULT_LIMIT = 200


# 2026-09-06, bug real encontrado en la primera corrida contra
# producción (Ferry, antes de que el batch grande llegara a lanzarse):
# comparar el string de país de TennisExplorer contra el de Nominatim
# SIN normalizar alias daba falsos positivos masivos -- de las primeras
# 26 filas "corregidas", 23 eran el MISMO país con nombre distinto
# ("USA" vs "United States", "Great Britain" vs "United Kingdom",
# "Dominican Rep." vs "Dominican Republic"), no coordenadas realmente
# mal geocodificadas. Solo 2/26 eran homónimos genuinos (Córdoba
# España/Argentina, Santiago Costa Rica/Chile).
#
# Hong Kong/China: tratado como el MISMO país a propósito -- Hong Kong
# es una región administrativa especial de China: un evento que
# TennisExplorer lista como "Hong Kong" y que Nominatim reverse-
# geocodifica como "China" (o viceversa) es la MISMA sede real, no un
# error de continente equivocado como Tigre/Etiopía o Toyota/Arabia
# Saudita -- no tiene sentido resetear una coordenada correcta por esto.
_COUNTRY_ALIASES: dict[str, str] = {
    "usa": "united states", "united states of america": "united states",
    "great britain": "united kingdom", "uk": "united kingdom",
    "dominican rep.": "dominican republic", "dominican rep": "dominican republic",
    "hong kong": "china",
    "czech rep.": "czech republic", "czech rep": "czech republic",
    "south korea": "korea, republic of", "korea": "korea, republic of",
    "russia": "russian federation",
    "uae": "united arab emirates",
}


def _normalize(name: str) -> str:
    key = name.strip().lower()
    return _COUNTRY_ALIASES.get(key, key)


def main() -> None:
    limit = _DEFAULT_LIMIT
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    init_db_historical()
    reverse_cache: dict[tuple[float, float], dict | None] = {}

    with db_historical.connection_context():
        base_query = Tournament.select().where(
            Tournament.country.is_null()
            & Tournament.lat.is_null(False)
            & Tournament.lon.is_null(False)
        )
        rows, _ = take_rotating_batch(
            base_query, Tournament.id, after_id=0, limit=limit,
            cursor_key="tournament_geocoding_audit_cursor",
        )
        print(f"Torneos a auditar esta corrida: {len(rows)}")

        n_ok = n_reset = n_not_found = n_reverse_failed = 0
        for t in rows:
            info = get_tournament_info(t.tournament_name, t.year, t.tour)
            if info is None or not info.get("country"):
                n_not_found += 1
                continue
            real_country = info["country"]

            coord_key = (round(t.lat, 3), round(t.lon, 3))
            if coord_key not in reverse_cache:
                reverse_cache[coord_key] = reverse_geocode(*coord_key)
            reverse = reverse_cache[coord_key]
            if reverse is None:
                n_reverse_failed += 1
                continue

            t.country = real_country
            if info.get("city") and not t.city:
                t.city = info["city"]
            t.fetched_at = datetime.now(timezone.utc)

            if _normalize(real_country) != _normalize(reverse["country"]):
                print(f"  MAL: {t.tournament_name} ({t.year}) -- real={real_country!r} "
                      f"vs coordenada guardada={reverse['country']!r} ({t.lat}, {t.lon}) -> reseteando")
                t.lat = None
                t.lon = None
                t.elevation_m = None
                n_reset += 1
            else:
                n_ok += 1

            t.save()

    print(f"\nResumen: correctos={n_ok}  corregidos(reseteados)={n_reset}  "
          f"sin_info_te={n_not_found}  reverse_geocode_fallo={n_reverse_failed}")


if __name__ == "__main__":
    main()
