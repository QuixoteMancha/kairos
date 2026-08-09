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
clave).

2026-08-09, pedido explícito del usuario -- "modifica las importaciones
para que sólo se utilice la tabla de matchhistory": se removieron
`populate_weather_for_charted_match`/`populate_weather_for_all_charted_matches`
y `populate_weather_for_match_history_row`/`populate_weather_for_all_match_history`
(y su throttle dedicado de Open-Meteo, ya sin uso) -- poblaban clima en
`ChartedMatch`/`TennisMatchHistory`, ambas tablas borradas (ya migradas
a `MatchHistory` vía `migrate_weather_to_match_history.py`, corrido y
retirado). Este módulo ahora solo cubre `TennisScannedMatch` (tracker
vivo de Kalshi, Categoría B, fuera de la consolidación)."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import peewee

from database.db import init_db_historical
from database.historical.tennis.models import Match as TennisScannedMatch
from database.historical.tennis.models import ProbabilityTick as TennisMatchProbabilityTick
from database.historical.tennis.models import MatchWeather as TennisMatchWeather
from outer_apis.scrapers.tennis.tournament_location import get_location, extract_city_candidate
from outer_apis.scrapers.weather import open_meteo


_DB_LOCK_RETRIES = 3
_DB_LOCK_RETRY_DELAY_S = 0.5


def _now():
    return datetime.now(timezone.utc)


def _get_location_with_geocode_cache(tournament_name: str | None) -> tuple[float, float] | None:
    """Igual que `get_location`, pero con un segundo nivel de fallback:
    la caché geocodeada vía Nominatim (`TournamentLocationCache`, ver
    `scripts/geocode_missing_tournament_locations.py`) para la cola
    larga de torneos challenger/ITF sin sede conocida a mano -- 2026-08-04,
    pedido explícito del usuario. Fail-closed en cascada: tabla
    estática -> caché geocodeada (si existe fila, sea resuelta o no) ->
    None. Nunca inventa una ubicación."""
    location = get_location(tournament_name)
    if location is not None:
        return location
    if not tournament_name:
        return None

    from database.historical.tennis import TournamentLocationCache

    candidate = extract_city_candidate(tournament_name)
    if candidate is None:
        return None  # evento de equipo/liga -- nunca se geocodifica, ver extract_city_candidate

    init_db_historical()
    cached = TournamentLocationCache.get_or_none(TournamentLocationCache.city_candidate == candidate)
    if cached is None or cached.lat is None or cached.lon is None:
        return None
    return cached.lat, cached.lon


def _create_weather_row_with_retry(**kwargs) -> None:
    """Bug real encontrado en producción 2026-08-01: `TennisMatchWeather`
    vive en `db_historical`, la misma DB que escriben concurrentemente
    `tennis_match_tick_worker` y `tennis_history_archiver_worker` -- bajo
    carga, SQLite (incluso en WAL) puede devolver "database is locked"
    para una escritura puntual que compite con otro proceso. Reintenta
    unas pocas veces con un delay corto antes de darse por vencido -- la
    alternativa (dejar que la excepción se propague) descartaba filas de
    clima real por una contención transitoria, no por un fallo real."""
    for attempt in range(_DB_LOCK_RETRIES):
        try:
            TennisMatchWeather.create(**kwargs)
            return
        except peewee.OperationalError as e:
            if "database is locked" not in str(e) or attempt == _DB_LOCK_RETRIES - 1:
                raise
            time.sleep(_DB_LOCK_RETRY_DELAY_S * (attempt + 1))


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
    location = _get_location_with_geocode_cache(match.tournament)
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
        _create_weather_row_with_retry(
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

    return {"total": len(matches), "processed": processed, "skipped": skipped, "errors": errors, "skip_reasons": reasons}

