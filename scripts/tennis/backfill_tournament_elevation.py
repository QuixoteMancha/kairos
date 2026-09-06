"""Backfill de `Tournament.elevation_m` -- 2026-09-06, pedido explícito
del usuario ("creo que depende... la altura donde se juega el torneo,
eso ha impactado a jugadoras como Svitolina, construye el campo y el
backfill ahora").

La altitud de una sede NUNCA cambia -- a diferencia del clima
(temperatura/humedad/viento por partido), esto se completa UNA SOLA VEZ
por coordenada, no por torneo/edición ni por partido. Se agrupa por
`(lat, lon)` redondeado a 3 decimales (~110m de precisión real, sin
sentido pedir una elevación distinta para el mismo punto geográfico por
un redondeo de piso) para no repetir requests idénticos a Open-Meteo
cuando varias ediciones/torneos comparten sede.

Fail-closed: un torneo sin `lat`/`lon` conocidos se salta (no hay
coordenada que consultar); una request que falla deja `elevation_m` en
NULL, nunca se asume/fabrica un valor -- se puede reintentar en una
corrida futura sin duplicar trabajo (los ya completados no se re-piden).

Uso: `python -m scripts.tennis.backfill_tournament_elevation`."""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from database.db import db_historical, init_db_historical
from database.historical.tennis.models import Tournament
from outer_apis.scrapers.weather import open_meteo


def main() -> None:
    init_db_historical()
    with db_historical.connection_context():
        pending = list(Tournament.select().where(
            Tournament.elevation_m.is_null(True)
            & Tournament.lat.is_null(False)
            & Tournament.lon.is_null(False)
        ))
        print(f"Torneos con coordenadas conocidas y elevación pendiente: {len(pending)}")

        by_coord: dict[tuple[float, float], list[Tournament]] = defaultdict(list)
        for t in pending:
            by_coord[(round(t.lat, 3), round(t.lon, 3))].append(t)
        print(f"Coordenadas únicas a consultar: {len(by_coord)}")

        n_ok = 0
        n_failed = 0
        for (lat, lon), tournaments in by_coord.items():
            elevation = open_meteo.get_elevation(lat, lon)
            open_meteo.throttle_sleep()
            if elevation is None:
                n_failed += len(tournaments)
                continue
            for t in tournaments:
                t.elevation_m = elevation
                t.save(only=[Tournament.elevation_m])
            n_ok += len(tournaments)
            names = ", ".join(f"{t.tournament_name} ({t.year})" for t in tournaments[:3])
            suffix = "..." if len(tournaments) > 3 else ""
            print(f"  ({lat}, {lon}) -> {elevation}m -- {names}{suffix}")

    print(f"\nResumen: torneos actualizados={n_ok}  fallidos/sin dato={n_failed}")


if __name__ == "__main__":
    main()
