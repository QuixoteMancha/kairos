"""Audita coordenadas de torneos contra un asentamiento de Nominatim.

Este auditor cubre una tercera clase de geocoding incorrecto: una consulta
con ciudad y país correctos puede devolver el centro de una provincia,
condado, parque o accidente geográfico homónimo. Es especialmente dañino
para ``Tournament.elevation_m`` y para ``MatchWeather``, que usan las mismas
coordenadas.

El script es deliberadamente DE SOLO LECTURA. No resetea ni actualiza una
fila, incluso si encuentra una discrepancia: deja evidencia en un JSON para
que una corrección posterior pueda ser revisada. Así una fuente temporalmente
distinta o un venue suburbano no puede borrar datos por accidente.

Para cada (ciudad, país) geocodificado:
  1. vuelve a consultar Nominatim, exigiendo un asentamiento local válido
     (``geocode_city``);
  2. compara la coordenada guardada con ese asentamiento;
  3. sólo para discrepancias de distancia, consulta la elevación de referencia
     de Open-Meteo y la incorpora como evidencia independiente.

Las consultas a Nominatim respetan su límite global de 1.1 s ya incorporado
en el geocoder. El trabajo se agrupa por ciudad/país para no pedir una vez
por cada edición del mismo torneo.

Uso:
  python -m scripts.tennis.audit_tournament_settlement_geocoding \
      --limit 100 --report /tmp/tournament_settlement_audit.json
  python -m scripts.tennis.audit_tournament_settlement_geocoding \
      --all --report /tmp/tournament_settlement_audit.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from database.db import db_historical, init_db_historical
from database.historical.tennis.models import Tournament
from outer_apis.scrapers.tennis.nominatim_geocoder import geocode_city, reverse_geocode
from outer_apis.scrapers.weather import open_meteo

# Diez kilómetros ya cubren con holgura un recinto urbano/suburbano. Por
# encima, una "ciudad" de torneo y el centro del asentamiento no deberían
# divergir sin una explicación concreta que se debe revisar manualmente.
_DISTANCE_SUSPECT_KM = 10.0
_ELEVATION_SUSPECT_M = 250.0
_NOT_A_REAL_COUNTRY = {"world"}
_LOCALITY_ADDRESS_KEYS = ("city", "town", "village", "hamlet", "municipality")


def _normalized_name(value: str) -> str:
    """Normalización estricta sólo para comparar el nombre de localidad."""
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(character for character in decomposed.lower() if character.isalnum())


def _reverse_matches_city(reverse: dict[str, Any], city: str) -> bool:
    """Indica si la dirección inversa identifica inequívocamente la ciudad."""
    address = reverse.get("address")
    if not isinstance(address, dict):
        return False
    wanted = _normalized_name(city)
    return bool(wanted) and any(
        _normalized_name(str(address[key])) == wanted
        for key in _LOCALITY_ADDRESS_KEYS
        if address.get(key)
    )


def haversine_km(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
    """Distancia geodésica, sin dependencia externa."""
    radius_km = 6371.0088
    phi_a, phi_b = math.radians(lat_a), math.radians(lat_b)
    delta_phi = math.radians(lat_b - lat_a)
    delta_lambda = math.radians(lon_b - lon_a)
    value = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi_a) * math.cos(phi_b) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * radius_km * math.asin(math.sqrt(value))


def _coord_key(tournament: Tournament) -> tuple[float, float]:
    return round(float(tournament.lat), 6), round(float(tournament.lon), 6)


def _city_country_key(tournament: Tournament) -> tuple[str, str]:
    return tournament.city.strip(), tournament.country.strip()


def _is_auditable(tournament: Tournament) -> bool:
    return bool(
        tournament.city and tournament.city.strip()
        and tournament.country and tournament.country.strip()
        and tournament.country.strip().lower() not in _NOT_A_REAL_COUNTRY
        and tournament.lat is not None and tournament.lon is not None
    )


def _group_tournaments(rows: Iterable[Tournament]) -> dict[tuple[str, str], list[Tournament]]:
    grouped: dict[tuple[str, str], list[Tournament]] = defaultdict(list)
    for tournament in rows:
        if _is_auditable(tournament):
            grouped[_city_country_key(tournament)].append(tournament)
    return dict(grouped)


def _stored_coordinate_groups(rows: Iterable[Tournament]) -> dict[tuple[float, float], list[Tournament]]:
    grouped: dict[tuple[float, float], list[Tournament]] = defaultdict(list)
    for tournament in rows:
        grouped[_coord_key(tournament)].append(tournament)
    return dict(grouped)


def _representative_rows(rows: Iterable[Tournament]) -> list[dict[str, Any]]:
    return [
        {"id": row.id, "year": row.year, "tournament_name": row.tournament_name,
         "elevation_m": row.elevation_m}
        for row in sorted(rows, key=lambda item: (item.year, item.id))[:10]
    ]


def _candidate_evidence(
    city: str,
    country: str,
    rows: list[Tournament],
    reference: dict[str, Any],
) -> list[dict[str, Any]]:
    """Genera evidencia para las coordenadas lejos del asentamiento.

    La elevación de referencia sólo se pide tras encontrar una diferencia de
    distancia; evita convertir una auditoría de Nominatim en un backfill
    masivo innecesario de Open-Meteo.
    """
    far_groups: list[tuple[tuple[float, float], list[Tournament], float]] = []
    for (lat, lon), grouped_rows in _stored_coordinate_groups(rows).items():
        distance_km = haversine_km(lat, lon, reference["lat"], reference["lon"])
        if distance_km >= _DISTANCE_SUSPECT_KM:
            far_groups.append(((lat, lon), grouped_rows, distance_km))
    if not far_groups:
        return []

    reference_elevation = open_meteo.get_elevation(reference["lat"], reference["lon"])
    # Mantiene el ritmo documentado del cliente de Open-Meteo incluso si se
    # usa este script en una corrida larga junto a otros backfills.
    open_meteo.throttle_sleep()

    evidence: list[dict[str, Any]] = []
    for (lat, lon), grouped_rows, distance_km in far_groups:
        stored_elevations = sorted({float(row.elevation_m) for row in grouped_rows if row.elevation_m is not None})
        elevation_deltas = (
            [abs(value - reference_elevation) for value in stored_elevations]
            if reference_elevation is not None else []
        )
        high_confidence = bool(
            elevation_deltas and max(elevation_deltas) >= _ELEVATION_SUSPECT_M
        )
        # La comparación entre dos búsquedas no basta: hay ciudades homónimas
        # dentro de un mismo país. Sólo para casos fuertes por distancia y
        # altura se comprueba además que la coordenada almacenada no pertenece
        # a la localidad declarada. Un fallo de reverse-geocode queda como
        # evidencia incompleta y nunca habilita una corrección automática.
        stored_reverse = None
        stored_coordinate_locality_matches: bool | None = None
        if high_confidence:
            stored_reverse = reverse_geocode(lat, lon)
            if stored_reverse is not None:
                stored_coordinate_locality_matches = _reverse_matches_city(stored_reverse, city)
        evidence.append({
            "city": city,
            "country": country,
            "stored_lat": lat,
            "stored_lon": lon,
            "distance_to_verified_settlement_km": round(distance_km, 2),
            "stored_elevation_m": stored_elevations,
            "reference_elevation_m": reference_elevation,
            "max_elevation_delta_m": round(max(elevation_deltas), 1) if elevation_deltas else None,
            "high_confidence": high_confidence,
            "stored_reverse": stored_reverse,
            "stored_coordinate_locality_matches": stored_coordinate_locality_matches,
            # Sólo esta categoría tiene prueba suficiente para un reset:
            # distancia + elevación + reverse que contradice la ciudad.
            "verified_wrong_coordinate": bool(
                high_confidence and stored_coordinate_locality_matches is False
            ),
            "tournament_count": len(grouped_rows),
            "examples": _representative_rows(grouped_rows),
        })
    return evidence


def audit(rows: Iterable[Tournament], limit: int | None = None) -> dict[str, Any]:
    """Ejecuta la auditoría sin escribir en base; separada para tests."""
    grouped = _group_tournaments(rows)
    groups = sorted(grouped.items(), key=lambda item: (item[0][1].lower(), item[0][0].lower()))
    if limit is not None:
        groups = groups[:limit]

    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only",
        "thresholds": {
            "distance_suspect_km": _DISTANCE_SUSPECT_KM,
            "elevation_suspect_m": _ELEVATION_SUSPECT_M,
        },
        "summary": {
            "city_country_groups": len(groups),
            "tournaments_in_groups": sum(len(group_rows) for _, group_rows in groups),
            "valid_settlement_reference": 0,
            "no_safe_settlement_result": 0,
            "coordinate_groups_far_from_settlement": 0,
            "high_confidence_coordinate_and_elevation_mismatches": 0,
            "verified_wrong_coordinate_groups": 0,
        },
        "candidates": [],
        "unresolved": [],
    }
    summary = report["summary"]

    for (city, country), group_rows in groups:
        reference = geocode_city(city, country=country)
        if reference is None:
            summary["no_safe_settlement_result"] += 1
            report["unresolved"].append({
                "city": city, "country": country, "tournament_count": len(group_rows),
                "reason": "Nominatim did not return a verified local settlement",
            })
            continue

        summary["valid_settlement_reference"] += 1
        evidence = _candidate_evidence(city, country, group_rows, reference)
        if not evidence:
            continue
        summary["coordinate_groups_far_from_settlement"] += len(evidence)
        summary["high_confidence_coordinate_and_elevation_mismatches"] += sum(
            1 for item in evidence if item["high_confidence"]
        )
        summary["verified_wrong_coordinate_groups"] += sum(
            1 for item in evidence if item["verified_wrong_coordinate"]
        )
        report["candidates"].append({
            "city": city,
            "country": country,
            "verified_settlement": {
                key: reference[key]
                for key in ("lat", "lon", "display_name", "class", "type", "place_rank", "candidate_rank")
            },
            "evidence": evidence,
        })

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--limit", type=int, default=100, help="máximo de pares ciudad/país (default: 100)")
    scope.add_argument("--all", action="store_true", help="auditar todos los pares ciudad/país")
    parser.add_argument(
        "--report", type=Path, default=Path("tournament_settlement_geocoding_audit.json"),
        help="archivo JSON de salida (default: %(default)s)",
    )
    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit debe ser positivo")

    init_db_historical()
    with db_historical.connection_context():
        rows = list(Tournament.select().where(
            Tournament.lat.is_null(False) & Tournament.lon.is_null(False)
        ))
        report = audit(rows, limit=None if args.all else args.limit)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = report["summary"]
    print(
        "Auditoría read-only terminada: "
        f"grupos={summary['city_country_groups']} "
        f"referencias_seguras={summary['valid_settlement_reference']} "
        f"sin_resultado_seguro={summary['no_safe_settlement_result']} "
        f"coordenadas_lejanas={summary['coordinate_groups_far_from_settlement']} "
        f"alta_confianza={summary['high_confidence_coordinate_and_elevation_mismatches']} "
        f"coordenada_verificada_mal={summary['verified_wrong_coordinate_groups']}"
    )
    print(f"Reporte: {args.report}")


if __name__ == "__main__":
    main()
