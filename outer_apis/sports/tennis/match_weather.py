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
from datetime import date, datetime, timezone
from typing import Any

import peewee

from database.db import init_db_historical
from database.historical.tennis.models import Match as TennisScannedMatch
from database.historical.tennis.models import MatchHistory
from database.historical.tennis.models import ProbabilityTick as TennisMatchProbabilityTick
from database.historical.tennis.models import MatchWeather as TennisMatchWeather
from outer_apis.scrapers.tennis.tournament_location import get_location
from outer_apis.scrapers.weather import open_meteo


_DB_LOCK_RETRIES = 3
_DB_LOCK_RETRY_DELAY_S = 0.5


def _now():
    return datetime.now(timezone.utc)


def _get_location_with_geocode_cache(tournament_name: str | None) -> tuple[float, float] | None:
    """Igual que `get_location` -- 2026-08-10, pedido explícito del
    usuario ("borra la base y reconstruyamos de cero"): el segundo nivel
    de fallback que existía acá (`TournamentLocation`, caché geocodeada
    vía Nominatim keyed por `city_candidate` de texto libre) quedó SIN
    equivalente real tras consolidar `WtaTournamentLevel`/
    `AtpTournamentLevel`/`TournamentLocation` en el modelo único
    `Tournament` (keyed por año+nombre de torneo, `city` NO-nulo) --
    granularidad distinta, no mapea 1:1. El script que poblaba esa
    caché (`scripts/tennis/geocode_missing_tournament_locations.py`) ya
    no existe en el repo (solo quedaba el .pyc). Degradado fail-closed:
    solo la tabla estática (`get_location`), sin caché geocodeada de
    cola larga -- deuda técnica documentada, no silenciosamente
    reinventada."""
    return get_location(tournament_name)


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


# --- Clima de `MatchHistory` (backfill histórico de TennisExplorer) ---
# 2026-08-16, pedido explícito del usuario: "agregar un worker que haga
# fetch de los datos climáticos, que revise cada 1 hora y haga fetch de
# los datos climáticos de los matches".
#
# GAP REAL que esto cierra: `MatchWeather.match_history` (FK, existe
# desde 2026-08-09) NO tenía ningún productor -- todo lo de arriba
# puebla `MatchWeather.match` (`TennisScannedMatch`, tracker vivo de
# Kalshi). El backfill masivo del catálogo llena `MatchHistory`, que
# quedaba sin clima.
#
# DIFERENCIA DE DISEÑO, deliberada y documentada: `MatchHistory` tiene
# `match_date` pero NO hora de inicio (TennisExplorer no la expone en el
# historial por jugador), así que acá NO se pueden calcular las 3 fases
# reales (before/during/end) -- esas dependen de `detected_at`/ticks/
# `settled_at`, que solo existen para partidos vivos de Kalshi. En vez
# de fabricar una hora arbitraria, se guarda UNA fila con el AGREGADO
# REAL DEL DÍA (`open_meteo.get_daily_weather`) bajo la fase explícita
# `match_day` -- nunca se confunde con las fases horarias reales.

_MATCH_HISTORY_WEATHER_PHASE = "match_day"


def _match_history_location(match) -> tuple[float, float] | None:
    """(lat, lon) para un partido de `MatchHistory`. Prioridad:
    `Tournament.lat`/`lon` reales (FK ya poblada, más cobertura y más
    preciso) y, si no están, la tabla estática por nombre de torneo
    (`get_location`, 73 sedes). Fail-closed: None si no hay ninguna de
    las dos -- nunca se adivina una sede."""
    tournament = match.tournament
    if tournament is not None:
        if tournament.lat is not None and tournament.lon is not None:
            return (tournament.lat, tournament.lon)
        located = get_location(tournament.tournament_name)
        if located is not None:
            return located
    return None


def populate_weather_for_match_history_row(match) -> dict[str, Any]:
    """Puebla la fila de clima del día para UN partido de
    `MatchHistory`, si no existe todavía (idempotente vía el índice
    único real `(match_history, phase)` + chequeo previo).

    Fail-closed en cada paso: sin fecha parseable, sin sede resoluble, o
    sin dato real de Open-Meteo, no escribe nada y reporta el motivo --
    nunca guarda una fila de clima fabricada."""
    if not match.match_date:
        return {"skipped": True, "reason": "sin match_date"}
    try:
        day = date.fromisoformat(str(match.match_date)[:10])
    except ValueError:
        return {"skipped": True, "reason": f"match_date no parseable: {match.match_date!r}"}

    location = _match_history_location(match)
    if location is None:
        return {"skipped": True, "reason": "sede del torneo no resoluble"}

    init_db_historical()
    exists = TennisMatchWeather.select().where(
        (TennisMatchWeather.match_history == match)
        & (TennisMatchWeather.phase == _MATCH_HISTORY_WEATHER_PHASE)
    ).exists()
    if exists:
        return {"skipped": True, "reason": "ya tiene clima"}

    lat, lon = location
    open_meteo.throttle_sleep()
    weather = open_meteo.get_daily_weather(lat, lon, day)
    if weather is None:
        return {"skipped": True, "reason": "Open-Meteo sin dato real para esa fecha/sede"}

    _create_weather_row_with_retry(
        match_history=match,
        phase=_MATCH_HISTORY_WEATHER_PHASE,
        observed_at=datetime(day.year, day.month, day.day, tzinfo=timezone.utc),
        temperature_c=weather["temperature_c"],
        humidity_pct=weather["humidity_pct"],
        wind_kmh=weather["wind_kmh"],
        fetched_at=_now(),
    )
    return {"skipped": False, "populated": True}


def populate_weather_for_match_history(limit: int | None = None) -> dict[str, Any]:
    """Recorre los partidos de `MatchHistory` que TODAVÍA no tienen
    clima y se los puebla -- pensado para correr como worker periódico
    (ver `workers/tennis_weather_worker.py`).

    Solo considera partidos con `match_date` y `tournament` no nulos (los
    únicos que pueden resolver fecha+sede real) y los procesa de los MÁS
    RECIENTES hacia atrás -- son los más relevantes para backtests y los
    que más probablemente tengan la sede ya poblada.

    `limit`: tope de partidos POR CORRIDA (default 500) -- el catálogo
    completo son decenas de miles de partidos y Open-Meteo tiene cupo
    real por hora/día (ver `open_meteo.throttle_sleep`); el worker corre
    cada hora, así que va completando de a tandas en vez de intentar
    todo de una y agotar el cupo.

    Corta limpio si se agota el cupo diario real de Open-Meteo
    (`DailyBudgetExhausted`) en vez de seguir pegándole a la API."""
    init_db_historical()
    batch_limit = 500 if limit is None else limit

    already = {
        w.match_history_id
        for w in TennisMatchWeather
        .select(TennisMatchWeather.match_history)
        .where(TennisMatchWeather.match_history.is_null(False))
    }

    query = (
        MatchHistory
        .select()
        .where(
            MatchHistory.match_date.is_null(False)
            & MatchHistory.tournament.is_null(False)
        )
        .order_by(MatchHistory.match_date.desc())
    )

    processed = skipped = errors = 0
    reasons: dict[str, int] = {}
    budget_exhausted = False
    for match in query:
        if processed + skipped >= batch_limit:
            break
        if match.id in already:
            continue
        try:
            result = populate_weather_for_match_history_row(match)
        except open_meteo.DailyBudgetExhausted:
            budget_exhausted = True
            break
        except Exception as e:
            errors += 1
            key = f"error: {type(e).__name__}"
            reasons[key] = reasons.get(key, 0) + 1
            continue
        if result.get("skipped"):
            skipped += 1
            reason = result.get("reason", "desconocido")
            reasons[reason] = reasons.get(reason, 0) + 1
        else:
            processed += 1

    return {
        "processed": processed, "skipped": skipped, "errors": errors,
        "budget_exhausted": budget_exhausted, "skip_reasons": reasons,
    }

