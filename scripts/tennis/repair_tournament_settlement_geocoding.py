"""Corrige coordenadas de torneos sólo después de evidencia revisada.

La auditoría de asentamientos es deliberadamente read-only. Este comando es
el segundo paso, explícito y acotado: recibe pares ``CIUDAD|PAIS`` aprobados
y revalida cada grupo de coordenadas antes de escribir. No acepta una mera
coincidencia de nombre, porque existen homónimos dentro de un país.

Para cada grupo exige simultáneamente:
  1. más de 10 km del asentamiento válido recién consultado;
  2. diferencia de elevación de al menos 250 m contra Open-Meteo;
  3. reverse-geocode de la coordenada antigua que no identifica la ciudad.

Sin ``--apply`` sólo produce el plan. Con ``--apply`` reemplaza coordenadas y
elevación por las verificadas, y borra únicamente el clima histórico ligado a
esos torneos para que ``tennis_weather_worker`` lo obtenga otra vez con la
sede correcta. Nunca inventa coordenadas ni borra clima sin las tres pruebas.

Ejemplo (primero sin --apply):
  python -m scripts.tennis.repair_tournament_settlement_geocoding \
    --target 'Brescia|Italy' --target 'Ourense|Spain' --target "L'Aquila|Italy"
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from database.db import db_historical, init_db_historical
from database.historical.tennis.models import MatchHistory, MatchWeather, Tournament
from outer_apis.scrapers.tennis.nominatim_geocoder import geocode_city, reverse_geocode
from outer_apis.scrapers.weather import open_meteo
from scripts.tennis.audit_tournament_settlement_geocoding import (
    _DISTANCE_SUSPECT_KM,
    _ELEVATION_SUSPECT_M,
    _normalized_name,
    _reverse_matches_city,
    haversine_km,
)


def _key(city: str, country: str) -> tuple[str, str]:
    return _normalized_name(city), _normalized_name(country)


def parse_target(value: str) -> tuple[str, str]:
    try:
        city, country = (part.strip() for part in value.split("|", 1))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use CIUDAD|PAIS") from exc
    if not city or not country:
        raise argparse.ArgumentTypeError("CIUDAD y PAIS son obligatorios")
    return city, country


def is_verified_wrong_coordinate(
    city: str,
    stored_lat: float,
    stored_lon: float,
    stored_elevations: Iterable[float],
    reference: dict[str, Any],
    reference_elevation: float | None,
    stored_reverse: dict[str, Any] | None,
) -> tuple[bool, dict[str, Any]]:
    """Clasifica sin escribir; devuelve evidencia reproducible del umbral."""
    distance_km = haversine_km(stored_lat, stored_lon, reference["lat"], reference["lon"])
    elevations = sorted({float(value) for value in stored_elevations})
    deltas = (
        [abs(value - reference_elevation) for value in elevations]
        if reference_elevation is not None else []
    )
    locality_matches = (
        _reverse_matches_city(stored_reverse, city)
        if stored_reverse is not None else None
    )
    evidence = {
        "stored_lat": stored_lat,
        "stored_lon": stored_lon,
        "distance_to_verified_settlement_km": round(distance_km, 2),
        "stored_elevation_m": elevations,
        "reference_elevation_m": reference_elevation,
        "max_elevation_delta_m": round(max(deltas), 1) if deltas else None,
        "stored_reverse": stored_reverse,
        "stored_coordinate_locality_matches": locality_matches,
    }
    verified = bool(
        distance_km >= _DISTANCE_SUSPECT_KM
        and deltas and max(deltas) >= _ELEVATION_SUSPECT_M
        and locality_matches is False
    )
    return verified, evidence


def build_plan(rows: Iterable[Tournament], targets: set[tuple[str, str]]) -> dict[str, Any]:
    """Consulta evidencia real y prepara cambios, sin modificar la base."""
    grouped: dict[tuple[str, str], list[Tournament]] = defaultdict(list)
    labels: dict[tuple[str, str], tuple[str, str]] = {}
    for row in rows:
        if not (row.city and row.country and row.lat is not None and row.lon is not None):
            continue
        key = _key(row.city, row.country)
        if key in targets:
            grouped[key].append(row)
            labels.setdefault(key, (row.city, row.country))

    plan: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "dry_run",
        "targets_requested": sorted("|".join(target) for target in targets),
        "targets": [],
        "tournament_ids_to_update": [],
    }
    for key, target_rows in sorted(grouped.items()):
        city, country = labels[key]
        item: dict[str, Any] = {"city": city, "country": country, "coordinate_groups": []}
        reference = geocode_city(city, country=country)
        if reference is None:
            item["reason"] = "no_safe_settlement_reference"
            plan["targets"].append(item)
            continue
        reference_elevation = open_meteo.get_elevation(reference["lat"], reference["lon"])
        open_meteo.throttle_sleep()
        if reference_elevation is None:
            item["reason"] = "no_reference_elevation"
            plan["targets"].append(item)
            continue
        by_coordinate: dict[tuple[float, float], list[Tournament]] = defaultdict(list)
        for row in target_rows:
            by_coordinate[(round(float(row.lat), 6), round(float(row.lon), 6))].append(row)
        approved_ids: list[int] = []
        for (lat, lon), coordinate_rows in by_coordinate.items():
            stored_reverse = reverse_geocode(lat, lon)
            verified, evidence = is_verified_wrong_coordinate(
                city, lat, lon,
                (row.elevation_m for row in coordinate_rows if row.elevation_m is not None),
                reference, reference_elevation, stored_reverse,
            )
            evidence["tournament_ids"] = sorted(row.id for row in coordinate_rows)
            evidence["verified_wrong_coordinate"] = verified
            item["coordinate_groups"].append(evidence)
            if verified:
                approved_ids.extend(evidence["tournament_ids"])
        item["verified_settlement"] = {
            key: reference[key]
            for key in ("lat", "lon", "display_name", "class", "type", "place_rank", "candidate_rank")
        }
        item["reference_elevation_m"] = reference_elevation
        item["tournament_ids_to_update"] = sorted(approved_ids)
        plan["tournament_ids_to_update"].extend(approved_ids)
        plan["targets"].append(item)
    plan["tournament_ids_to_update"] = sorted(set(plan["tournament_ids_to_update"]))
    return plan


def apply_plan(plan: dict[str, Any]) -> dict[str, int]:
    """Aplica exclusivamente grupos aprobados por ``build_plan``."""
    updated_tournament_ids: list[int] = []
    deleted_weather = 0
    with db_historical.atomic():
        for target in plan["targets"]:
            reference = target.get("verified_settlement")
            ids = target.get("tournament_ids_to_update", [])
            elevation = target.get("reference_elevation_m")
            if not reference or not ids or elevation is None:
                continue
            Tournament.update(
                lat=reference["lat"], lon=reference["lon"], elevation_m=elevation,
                fetched_at=datetime.now(timezone.utc),
            ).where(Tournament.id.in_(ids)).execute()
            updated_tournament_ids.extend(ids)

        if updated_tournament_ids:
            affected_histories = MatchHistory.select(MatchHistory.id).where(
                MatchHistory.tournament.in_(updated_tournament_ids)
            )
            deleted_weather = MatchWeather.delete().where(
                MatchWeather.match_history.in_(affected_histories)).execute()
    return {
        "tournaments_updated": len(updated_tournament_ids),
        "weather_rows_deleted_for_refresh": deleted_weather,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", action="append", type=parse_target, required=True,
                        help="par aprobado como CIUDAD|PAIS; puede repetirse")
    parser.add_argument("--apply", action="store_true", help="escribe sólo grupos con evidencia completa")
    parser.add_argument("--report", type=Path,
                        default=Path("tournament_settlement_geocoding_repair_plan.json"))
    args = parser.parse_args()
    targets = {_key(city, country) for city, country in args.target}

    init_db_historical()
    with db_historical.connection_context():
        rows = list(Tournament.select().where(
            Tournament.lat.is_null(False) & Tournament.lon.is_null(False)
        ))
        plan = build_plan(rows, targets)
        if args.apply:
            plan["mode"] = "applied"
            plan["result"] = apply_plan(plan)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "mode": plan["mode"],
        "tournaments_to_update": len(plan["tournament_ids_to_update"]),
        "result": plan.get("result"),
        "report": str(args.report),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
