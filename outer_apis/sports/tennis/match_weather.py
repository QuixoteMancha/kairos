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

import time
from datetime import datetime, timezone
from typing import Any

import peewee

from database.db import init_db_historical
from database.historical.tennis.tennisscannedmatch import TennisScannedMatch
from database.historical.tennis.tennismatchprobabilitytick import TennisMatchProbabilityTick
from database.historical.tennis.tennismatchweather import TennisMatchWeather
from outer_apis.scrapers.tennis.tournament_location import get_location
from outer_apis.scrapers.weather import open_meteo


_DB_LOCK_RETRIES = 3
_DB_LOCK_RETRY_DELAY_S = 0.5


def _now():
    return datetime.now(timezone.utc)


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


# ── ChartedMatch (2026-08-02, pedido explícito del usuario) ────────────
#
# A DIFERENCIA de `TennisScannedMatch` (arriba, 3 fases via ticks reales
# de Kalshi), un `ChartedMatch` (Match Charting Project de Sackmann) NO
# tiene hora de inicio real ni duración real -- `Time` solo está
# poblado en ~54% de los partidos del CSV, y ni siquiera ahí hay
# duración. Fabricar "durante"/"al final" sin esos datos violaría el
# principio de este proyecto de nunca inventar un dato. Se puebla UN
# solo snapshot representativo por partido, a una hora fija documentada
# (14:00 UTC del día real del partido) -- útil para ver clima típico del
# día, NO el clima exacto en el momento en que se jugó cada punto.

_CHARTED_MATCH_SNAPSHOT_HOUR_UTC = 14


def populate_weather_for_charted_match(match) -> dict[str, Any]:
    """Puebla el único snapshot de clima de un `ChartedMatch` si no lo
    tiene todavía (idempotente vía `weather_fetched_at`). Fail-closed:
    no escribe nada si el torneo no está en `tournament_location.py` o
    si la request de clima falla -- deja `weather_fetched_at` en NULL,
    reintentable después (no es un fallo permanente registrado como tal,
    a diferencia de si escribiéramos ceros)."""
    if match.weather_fetched_at is not None:
        return {"skipped": True, "reason": "ya tiene clima"}

    location = get_location(match.tournament)
    if location is None:
        return {"skipped": True, "reason": f"torneo no reconocido: {match.tournament!r}"}

    lat, lon = location
    when = datetime(
        match.match_date.year, match.match_date.month, match.match_date.day,
        _CHARTED_MATCH_SNAPSHOT_HOUR_UTC, tzinfo=timezone.utc,
    )

    weather = open_meteo.get_weather_at(lat, lon, when)
    if weather is None:
        return {"skipped": True, "reason": "clima real no disponible (fecha fuera de rango o request fallida)"}

    init_db_historical()
    match.temperature_c = weather.get("temperature_c")
    match.humidity_pct = weather.get("humidity_pct")
    match.wind_kmh = weather.get("wind_kmh")
    match.weather_fetched_at = _now()
    match.save()

    return {"skipped": False, "populated": True}


# Rate limit real de Open-Meteo (2026-08-02, investigado tras pregunta
# explícita del usuario -- NO se había tenido en cuenta al lanzar el
# primer backfill de 7744 partidos): free tier documentado ~600
# requests/minuto, 5000/hora, 10000/día. Este loop puede recorrer miles
# de `ChartedMatch` de una sola corrida (a diferencia de
# `populate_weather_for_all_resolved_matches`, que trabaja sobre un
# puñado de partidos en vivo por ciclo) -- necesita throttle explícito,
# no confiar en la latencia de red incidental como único freno.
_OPEN_METEO_MIN_INTERVAL_S = 0.15  # ~400 req/min, con margen bajo 600/min


def populate_weather_for_all_charted_matches() -> dict[str, Any]:
    """Recorre TODOS los `ChartedMatch` sin clima poblado -- mismo
    patrón que `populate_weather_for_all_resolved_matches`, con
    throttle explícito (ver `_OPEN_METEO_MIN_INTERVAL_S`) porque acá el
    volumen puede ser de miles de partidos en una sola corrida."""
    from database.historical.tennis.chartedmatch import ChartedMatch

    init_db_historical()
    # Materializado a lista ANTES del loop -- el filtro es "sin clima
    # poblado", así que la query se re-evalúa con resultados distintos
    # a medida que el loop escribe clima; sin esto, `matches.count()`
    # de abajo contaría de menos (bug real encontrado por el propio
    # test de este cambio).
    matches = list(ChartedMatch.select().where(ChartedMatch.weather_fetched_at.is_null()))

    processed = skipped = errors = 0
    reasons: dict[str, int] = {}
    for match in matches:
        try:
            result = populate_weather_for_charted_match(match)
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
            time.sleep(_OPEN_METEO_MIN_INTERVAL_S)  # solo tras un fetch real, no tras un skip (no pega la API)

    return {"total": len(matches), "processed": processed, "skipped": skipped, "errors": errors, "skip_reasons": reasons}


def populate_weather_for_match_history_row(row) -> dict[str, Any]:
    """Igual que `populate_weather_for_charted_match` pero para filas de
    `TennisMatchHistory` (2026-08-03, pedido explícito del usuario tras
    el backfill masivo top-200 WTA: "luego hacer fetch de datos
    climáticos de match") -- mismo snapshot único a
    `_CHARTED_MATCH_SNAPSHOT_HOUR_UTC` UTC (tampoco hay hora real de
    inicio acá), misma tabla de ubicación, mismo criterio fail-closed.

    Diferencia real con ChartedMatch: `match_date` acá es un CharField
    "YYYY-MM-DD" (no un DateField real), hay que parsearlo a mano."""
    if row.weather_fetched_at is not None:
        return {"skipped": True, "reason": "ya tiene clima"}

    location = get_location(row.tournament)
    if location is None:
        return {"skipped": True, "reason": f"torneo no reconocido: {row.tournament!r}"}

    try:
        year, month, day = (int(p) for p in row.match_date.split("-"))
    except (ValueError, AttributeError):
        return {"skipped": True, "reason": f"match_date no parseable: {row.match_date!r}"}

    lat, lon = location
    when = datetime(year, month, day, _CHARTED_MATCH_SNAPSHOT_HOUR_UTC, tzinfo=timezone.utc)

    weather = open_meteo.get_weather_at(lat, lon, when)
    if weather is None:
        return {"skipped": True, "reason": "clima real no disponible (fecha fuera de rango o request fallida)"}

    init_db_historical()
    row.temperature_c = weather.get("temperature_c")
    row.humidity_pct = weather.get("humidity_pct")
    row.wind_kmh = weather.get("wind_kmh")
    row.weather_fetched_at = _now()
    row.save()

    return {"skipped": False, "populated": True}


def populate_weather_for_all_match_history() -> dict[str, Any]:
    """Recorre TODAS las filas de `TennisMatchHistory` sin clima
    poblado -- mismo patrón/throttle que
    `populate_weather_for_all_charted_matches`, para el backfill masivo
    top-200 WTA (potencialmente decenas de miles de filas)."""
    from database.historical.tennis.tennismatchhistory import TennisMatchHistory

    init_db_historical()
    rows = list(TennisMatchHistory.select().where(TennisMatchHistory.weather_fetched_at.is_null()))

    processed = skipped = errors = 0
    reasons: dict[str, int] = {}
    for row in rows:
        try:
            result = populate_weather_for_match_history_row(row)
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
            time.sleep(_OPEN_METEO_MIN_INTERVAL_S)

    return {"total": len(rows), "processed": processed, "skipped": skipped, "errors": errors, "skip_reasons": reasons}
