"""Puebla clima real (temperatura/humedad/viento) para partidos de MLB
ya jugados -- 2026-08-05, pedido explícito del usuario ("adapta el
backfill de clima de tenis para los otros deportes que tiene el
sistema").

DIFERENCIA REAL con tenis (`outer_apis/sports/tennis/match_weather.py`):
tenis nunca tiene hora real de inicio, así que usa un snapshot
aproximado a una hora fija (14:00 UTC). MLB SÍ trae la hora real del
primer lanzamiento (`game_datetime_utc`, campo `gameDate` de
statsapi.mlb.com) -- el snapshot de clima se pide exactamente en ese
momento real, sin aproximar nada.

Ubicación: `outer_apis/scrapers/baseball/team_location.py` (equipo
local -> estadio real), fail-closed si el equipo no está en la tabla.

Clima: `outer_apis/scrapers/weather/open_meteo.py` (mismo cliente real
que tenis, gratis, sin clave) + mismo throttle real de ventana rodante
(`outer_apis.scrapers.weather.open_meteo.throttle_sleep` -- 2026-08-09,
movido de `tennis/match_weather.py` a este módulo compartido, es el
mismo servidor Open-Meteo con el mismo presupuesto real compartido
entre todos los deportes, no un límite separado por deporte)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from database.db import init_db_historical
from outer_apis.scrapers.baseball.team_location import get_team_location
from outer_apis.scrapers.weather import open_meteo
from outer_apis.scrapers.weather.open_meteo import throttle_sleep, DailyBudgetExhausted


def _now():
    return datetime.now(timezone.utc)


def populate_weather_for_mlb_game(row) -> dict[str, Any]:
    """Puebla el único snapshot de clima de un `MlbGameHistory` si no lo
    tiene todavía (idempotente vía `weather_fetched_at`). Fail-closed:
    equipo local no reconocido, `game_datetime_utc` no parseable, o
    clima real no disponible (fecha fuera del rango de Open-Meteo) ->
    se saltea, nunca se fabrica un valor."""
    if row.weather_fetched_at is not None:
        return {"skipped": True, "reason": "ya tiene clima"}

    location = get_team_location(row.home_team)
    if location is None:
        return {"skipped": True, "reason": f"equipo local no reconocido: {row.home_team!r}"}

    if not row.game_datetime_utc:
        return {"skipped": True, "reason": "sin game_datetime_utc real (partido de un backfill previo a este fix)"}
    try:
        when = datetime.fromisoformat(row.game_datetime_utc.replace("Z", "+00:00"))
    except ValueError:
        return {"skipped": True, "reason": f"game_datetime_utc no parseable: {row.game_datetime_utc!r}"}

    lat, lon = location
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


def populate_weather_for_all_mlb_games() -> dict[str, Any]:
    """Recorre TODAS las filas de `MlbGameHistory` sin clima poblado --
    mismo patrón/throttle que
    `outer_apis.sports.tennis.match_weather.populate_weather_for_all_match_history`
    (limitador real de ventana rodante hora+día, corta con
    `DailyBudgetExhausted` en vez de seguir pegándole a Open-Meteo)."""
    from database.historical.baseball import MlbGameHistory

    init_db_historical()
    rows = list(MlbGameHistory.select().where(MlbGameHistory.weather_fetched_at.is_null()))

    processed = skipped = errors = 0
    reasons: dict[str, int] = {}
    budget_exhausted = False
    for row in rows:
        try:
            result = populate_weather_for_mlb_game(row)
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
            try:
                throttle_sleep()
            except DailyBudgetExhausted:
                budget_exhausted = True
                break

    return {
        "total": len(rows), "processed": processed, "skipped": skipped, "errors": errors,
        "skip_reasons": reasons, "daily_budget_exhausted": budget_exhausted,
    }
