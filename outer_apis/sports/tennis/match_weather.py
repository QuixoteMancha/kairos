"""Puebla clima real (temperatura/humedad/viento) en 3 momentos de cada
partido de tenis ya resuelto -- pedido explícito del usuario 2026-08-01.

Los 3 momentos, con su definición REAL (nunca inventada):
  - "before": `TennisScannedMatch.detected_at` -- cuándo el sistema vio
    el partido por primera vez (real, pero puede ser horas/días antes
    del inicio real, según cuándo Kalshi abrió el mercado).
  - "during": punto medio real entre el primer tick observado
    (`TennisMatchProbabilityTick` más antiguo de ese partido) y el
    settlement (`settled_at`) -- proxy razonable de "durante el
    partido" ya que Kairos no tiene la hora exacta de inicio real del
    partido en ningún lado, documentado honestamente como aproximación,
    no como el punto medio exacto del partido real.
  - "end": `TennisScannedMatch.settled_at` -- cuándo el mercado resolvió
    (real).

Ubicación: `outer_apis/scrapers/tennis/tournament_location.py` (torneo ->
lat/lon), fail-closed si el torneo no está registrado en la tabla o no
se pudo determinar al momento del registro del partido (ver
`outer_apis/kalshi/markets/tennis.py`).

Clima: `outer_apis/scrapers/weather/open_meteo.py` (real, gratuito, sin
clave)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from database.db import init_db_historical
from database.historical.tennis.tennisscannedmatch import TennisScannedMatch
from database.historical.tennis.tennismatchprobabilitytick import TennisMatchProbabilityTick
from database.historical.tennis.tennismatchweather import TennisMatchWeather
from outer_apis.scrapers.tennis.tournament_location import get_location
from outer_apis.scrapers.weather import open_meteo


def _now():
    return datetime.now(timezone.utc)


def _phase_timestamps(match: TennisScannedMatch) -> dict[str, datetime] | None:
    """Fail-closed: None si falta `settled_at` (partido no resuelto) o no
    hay ningún tick registrado (no se puede calcular "during" sin al
    menos un tick real)."""
    if match.settled_at is None:
        return None

    detected_at = match.detected_at
    settled_at = match.settled_at
    if detected_at.tzinfo is None:
        detected_at = detected_at.replace(tzinfo=timezone.utc)
    if settled_at.tzinfo is None:
        settled_at = settled_at.replace(tzinfo=timezone.utc)

    first_tick = (
        TennisMatchProbabilityTick
        .select()
        .where(TennisMatchProbabilityTick.match == match)
        .order_by(TennisMatchProbabilityTick.observed_at.asc())
        .first()
    )
    if first_tick is None:
        return None
    first_tick_at = first_tick.observed_at
    if first_tick_at.tzinfo is None:
        first_tick_at = first_tick_at.replace(tzinfo=timezone.utc)

    midpoint = first_tick_at + (settled_at - first_tick_at) / 2

    return {"before": detected_at, "during": midpoint, "end": settled_at}


def populate_weather_for_match(match: TennisScannedMatch) -> dict[str, Any]:
    """Puebla las 3 filas de clima (before/during/end) para UN partido ya
    resuelto, si no existen todavía (idempotente vía índice único
    (match, phase) + get_or_create). Fail-closed en cada fase
    independiente: si el clima de una fase no se pudo obtener (fecha
    fuera del rango de Open-Meteo, request fallida), esa fase se saltea,
    las demás se intentan igual."""
    location = get_location(match.tournament)
    if location is None:
        return {"skipped": True, "reason": f"torneo no reconocido: {match.tournament!r}"}

    timestamps = _phase_timestamps(match)
    if timestamps is None:
        return {"skipped": True, "reason": "partido sin resolver o sin ticks reales"}

    lat, lon = location
    init_db_historical()
    fetched_at = _now()
    populated = []
    for phase, when in timestamps.items():
        exists = TennisMatchWeather.select().where(
            (TennisMatchWeather.match == match) & (TennisMatchWeather.phase == phase)
        ).exists()
        if exists:
            continue

        weather = open_meteo.get_weather_at(lat, lon, when)
        TennisMatchWeather.create(
            match=match, phase=phase, observed_at=when,
            temperature_c=(weather or {}).get("temperature_c"),
            humidity_pct=(weather or {}).get("humidity_pct"),
            wind_kmh=(weather or {}).get("wind_kmh"),
            fetched_at=fetched_at,
        )
        populated.append(phase)

    return {"skipped": False, "populated_phases": populated}


def populate_weather_for_all_resolved_matches() -> dict[str, Any]:
    """Recorre TODOS los partidos ya resueltos (`winner` no NULL) y les
    puebla el clima si falta -- pensado para correr como script/worker
    periódico (ver scripts/populate_match_weather.py)."""
    init_db_historical()
    matches = TennisScannedMatch.select().where(TennisScannedMatch.winner.is_null(False))

    processed = skipped = errors = 0
    reasons: dict[str, int] = {}
    for match in matches:
        try:
            result = populate_weather_for_match(match)
        except Exception as e:
            errors += 1
            reasons[f"error: {e}"] = reasons.get(f"error: {e}", 0) + 1
            continue
        if result.get("skipped"):
            skipped += 1
            reason = result.get("reason", "desconocido")
            reasons[reason] = reasons.get(reason, 0) + 1
        else:
            processed += 1

    return {"total": matches.count(), "processed": processed, "skipped": skipped, "errors": errors, "skip_reasons": reasons}
