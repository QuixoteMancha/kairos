"""Migra clima real de las 3 fuentes existentes a `MatchWeather.match_history`
-- 2026-08-09, pedido explícito del usuario: "la idea es que cuando
haga query de algún match se pueda obtener el clima del match... algo
como match.weather.humidity".

FUENTES (cada una con su propio criterio de identidad, mismo patrón ya
probado en `migrate_charted_stats_to_match_stats.py`: par de nombres +
fecha más cercana dentro de tolerancia, fail-closed ante ambigüedad):

  1. `ChartedMatch` (3,913 filas con clima real) -- nombre completo real
     de AMBOS lados + fecha exacta, igual que la migración de sus stats.
  2. `TennisMatchHistory` (21,113 filas con clima real, 2/partido) --
     SOLO `player_name` es nombre completo cierto (`opponent_name` viene
     abreviado) -- se resuelve por 1 nombre conocido + fecha, buscando
     en qué lado de `MatchHistory` aparece.
  3. `Match`/`TennisScannedMatch` vía `MatchWeather.match` (233 filas,
     tracking vivo de Kalshi) -- nombre completo real de ambos lados
     (viene del ticker), + `close_time`/`detected_at` como fecha.

`phase`: las 3 fuentes de arriba son snapshots ÚNICOS (no 3 fases
before/during/end como el tracking en vivo) -- se guardan como
`phase="during"` (aproximación razonable, "el clima del partido", ver
discusión con el usuario). El tracking vivo real (`Match`) SÍ mantiene
sus 3 fases originales al migrarse.

Idempotente por (`match_history`, `phase`) -- el índice único de la
tabla ya lo garantiza a nivel de DB, pero se chequea antes para no
depender de que la escritura falle."""

from __future__ import annotations

from datetime import date, datetime, timezone

from scripts.tennis.migrate_charted_stats_to_match_stats import build_match_history_index, resolve_match_history_id, _parse_date


def resolve_match_history_id_by_one_name(
    player_name: str, event_date: date, index_by_name: dict,
) -> tuple[int | None, str]:
    """Igual que `resolve_match_history_id` pero para fuentes donde SOLO
    se conoce 1 nombre completo real (`TennisMatchHistory`, rival
    abreviado) -- `index_by_name`: {name.lower(): [(id, date), ...]}.
    Fail-closed: None si no hay candidato único dentro de tolerancia."""
    candidates = index_by_name.get(player_name.lower(), [])
    within_tolerance = [
        (mh_id, d) for mh_id, d in candidates
        if abs((d - event_date).days) <= 14
    ]
    if not within_tolerance:
        return None, "no_candidate"
    if len(within_tolerance) == 1:
        return within_tolerance[0][0], "resolved"
    within_tolerance.sort(key=lambda t: abs((t[1] - event_date).days))
    best_diff = abs((within_tolerance[0][1] - event_date).days)
    tied = [c for c in within_tolerance if abs((c[1] - event_date).days) == best_diff]
    if len(tied) == 1:
        return tied[0][0], "resolved"
    return None, "ambiguous"


def build_match_history_index_by_name(rows: list[dict]) -> dict:
    """rows: los mismos dicts que `build_match_history_index` --
    reindexa por CADA nombre por separado (player_a Y player_b), no por
    el par, para poder resolver desde 1 solo nombre conocido."""
    index: dict[str, list] = {}
    for r in rows:
        d = _parse_date(r["match_date"])
        if d is None:
            continue
        for name in (r["player_a_name"], r["player_b_name"]):
            index.setdefault(name.lower(), []).append((r["id"], d))
    return index


def _weather_exists(MatchWeather, match_history_id: int, phase: str) -> bool:
    return MatchWeather.select().where(
        (MatchWeather.match_history == match_history_id) & (MatchWeather.phase == phase)
    ).exists()


def migrate_charted_match_weather() -> dict:
    from database.db import init_db_historical, db_historical
    from database.historical.tennis import ChartedMatch, MatchHistory
    from database.historical.tennis.models import MatchWeather

    init_db_historical()
    with db_historical.connection_context():
        mh_rows = [
            {"id": r.id, "player_a_name": r.player_a_name, "player_b_name": r.player_b_name, "match_date": r.match_date}
            for r in MatchHistory.select(MatchHistory.id, MatchHistory.player_a_name, MatchHistory.player_b_name, MatchHistory.match_date)
        ]
        charted = [
            {"player_a_name": r.player_a_name, "player_b_name": r.player_b_name, "match_date": r.match_date,
             "temperature_c": r.temperature_c, "humidity_pct": r.humidity_pct, "wind_kmh": r.wind_kmh,
             "weather_fetched_at": r.weather_fetched_at}
            for r in ChartedMatch.select().where(ChartedMatch.temperature_c.is_null(False))
        ]

    index = build_match_history_index(mh_rows)
    created = already_exists = no_candidate = ambiguous = 0
    with db_historical.connection_context():
        for c in charted:
            d = _parse_date(c["match_date"])
            if d is None:
                no_candidate += 1
                continue
            mh_id, reason = resolve_match_history_id(c["player_a_name"], c["player_b_name"], d, index)
            if mh_id is None:
                if reason == "ambiguous":
                    ambiguous += 1
                else:
                    no_candidate += 1
                continue
            if _weather_exists(MatchWeather, mh_id, "during"):
                already_exists += 1
                continue
            MatchWeather.create(
                match_history=mh_id, phase="during",
                observed_at=c["weather_fetched_at"] or datetime.now(timezone.utc),
                temperature_c=c["temperature_c"], humidity_pct=c["humidity_pct"], wind_kmh=c["wind_kmh"],
                fetched_at=c["weather_fetched_at"],
            )
            created += 1
    return {"source": "charted_match", "created": created, "already_exists": already_exists,
            "no_candidate": no_candidate, "ambiguous": ambiguous}


def migrate_tennis_match_history_weather() -> dict:
    from database.db import init_db_historical, db_historical
    from database.historical.tennis import TennisMatchHistory, MatchHistory
    from database.historical.tennis.models import MatchWeather

    init_db_historical()
    with db_historical.connection_context():
        mh_rows = [
            {"id": r.id, "player_a_name": r.player_a_name, "player_b_name": r.player_b_name, "match_date": r.match_date}
            for r in MatchHistory.select(MatchHistory.id, MatchHistory.player_a_name, MatchHistory.player_b_name, MatchHistory.match_date)
        ]
        weather_rows = [
            {"player_name": r.player_name, "match_date": r.match_date, "temperature_c": r.temperature_c,
             "humidity_pct": r.humidity_pct, "wind_kmh": r.wind_kmh, "weather_fetched_at": r.weather_fetched_at}
            for r in TennisMatchHistory.select().where(TennisMatchHistory.temperature_c.is_null(False))
        ]

    index_by_name = build_match_history_index_by_name(mh_rows)
    created = already_exists = no_candidate = ambiguous = 0
    with db_historical.connection_context():
        for w in weather_rows:
            d = _parse_date(w["match_date"])
            if d is None:
                no_candidate += 1
                continue
            mh_id, reason = resolve_match_history_id_by_one_name(w["player_name"], d, index_by_name)
            if mh_id is None:
                if reason == "ambiguous":
                    ambiguous += 1
                else:
                    no_candidate += 1
                continue
            if _weather_exists(MatchWeather, mh_id, "during"):
                already_exists += 1
                continue
            MatchWeather.create(
                match_history=mh_id, phase="during",
                observed_at=w["weather_fetched_at"] or datetime.now(timezone.utc),
                temperature_c=w["temperature_c"], humidity_pct=w["humidity_pct"], wind_kmh=w["wind_kmh"],
                fetched_at=w["weather_fetched_at"],
            )
            created += 1
    return {"source": "tennis_match_history", "created": created, "already_exists": already_exists,
            "no_candidate": no_candidate, "ambiguous": ambiguous}


def migrate_live_match_weather() -> dict:
    """Vincula las 233 filas ya existentes de `MatchWeather` (`match`,
    tracking vivo de Kalshi) a `MatchHistory` -- conserva las 3 fases
    reales (before/during/end), a diferencia de las otras 2 fuentes
    (snapshot único)."""
    from database.db import init_db_historical, db_historical
    from database.historical.tennis import MatchHistory
    from database.historical.tennis.models import MatchWeather

    init_db_historical()
    with db_historical.connection_context():
        mh_rows = [
            {"id": r.id, "player_a_name": r.player_a_name, "player_b_name": r.player_b_name, "match_date": r.match_date}
            for r in MatchHistory.select(MatchHistory.id, MatchHistory.player_a_name, MatchHistory.player_b_name, MatchHistory.match_date)
        ]
        live_rows = list(MatchWeather.select().where(
            MatchWeather.match.is_null(False) & MatchWeather.match_history.is_null()
        ))

    index = build_match_history_index(mh_rows)
    linked = already_exists = no_candidate = ambiguous = 0
    with db_historical.connection_context():
        for w in live_rows:
            match = w.match
            raw_date = (match.close_time or "")[:10] or match.detected_at.date().isoformat()
            d = _parse_date(raw_date)
            if d is None:
                no_candidate += 1
                continue
            mh_id, reason = resolve_match_history_id(match.player_a_name, match.player_b_name, d, index)
            if mh_id is None:
                if reason == "ambiguous":
                    ambiguous += 1
                else:
                    no_candidate += 1
                continue
            if _weather_exists(MatchWeather, mh_id, w.phase):
                already_exists += 1
                continue
            w.match_history = mh_id
            w.save()
            linked += 1
    return {"source": "live_match_tracking", "linked": linked, "already_exists": already_exists,
            "no_candidate": no_candidate, "ambiguous": ambiguous}


def run_all() -> dict:
    results = {
        "charted_match": migrate_charted_match_weather(),
        "tennis_match_history": migrate_tennis_match_history_weather(),
        "live_match_tracking": migrate_live_match_weather(),
    }
    print(f"[migrate_weather_to_match_history] {results}", flush=True)
    return results


if __name__ == "__main__":
    import json
    print(json.dumps(run_all(), indent=2, ensure_ascii=False))
