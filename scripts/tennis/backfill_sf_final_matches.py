"""
Backfill completo a DB de los partidos SF/Final ya escaneados por
`scripts/sf_final_scan.py` -- pedido explícito del usuario 2026-08-02:
"en base debemos tener estos matches, con los scores, los players, los
datos de clima (antes/durante/al final) + el historial de probabilidades
de Kalshi".

Reutiliza `sf_final_scan_result.json` (YA fetcheado, incluye
`flashscore_stats` -- CERO costo adicional de proxy BrightData acá) y
agrega, por cada partido:

  1. `TennisScannedMatch` -- registro retroactivo (idempotente por
     `market_id=event_ticker`). `player_a_rank_at_match`/
     `player_b_rank_at_match` quedan `None` a propósito -- NO tenemos el
     ranking real al momento histórico de estos partidos (serían
     rankings de HOY si se recalcularan, que ya sabemos que es una
     aproximación -- acá se prefiere `None` honesto en vez de guardar un
     valor que parecería exacto en la columna "_at_match").
     `detected_at` = timestamp del trade MÁS VIEJO real de Kalshi (mejor
     proxy real disponible de "cuándo pudo verse el mercado", más
     preciso que inventar una fecha). `settled_at` = timestamp del
     último trade real.
  2. `TennisMatchProbabilityTick` -- UN tick por cada trade REAL de la
     pierna A (ticker_a) del trade tape de Kalshi (`market_price_a` =
     precio real, `model_fair_a` = None -- NO se fabrica un fair price
     histórico, gap honesto y documentado).
  3. `TennisMatchStats` -- `flashscore_stats` ya fetcheado (aces/saque%/
     etc.) + `score` real set-por-set de Tennis Abstract (DB-first, cache
     ya tibio de los cálculos de fatiga/edad de esta misma sesión) bajo
     la key `"match_score"` del mismo JSON blob.
  4. Dispara `populate_weather_for_match` (YA EXISTENTE, ver
     `outer_apis/sports/tennis/match_weather.py`) -- funciona sin
     cambios una vez que 1-2 están poblados (necesita `detected_at`,
     `settled_at`, y al menos un tick real).

Fail-closed por partido y por paso: un fallo en cualquiera de los 4
pasos para un partido no aborta el resto del backfill."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from database.db import init_db_historical
from database.historical.tennis.models import Match as TennisScannedMatch
from database.historical.tennis.models import ProbabilityTick as TennisMatchProbabilityTick
from database.historical.tennis.matchstats import save_match_stats, has_match_stats
from outer_apis.sports.tennis.match_weather import populate_weather_for_match
from outer_apis.sports.tennis.recent_results_cache import fetch_and_cache
from outer_apis.kalshi.client import KalshiAPI


def _parse_trades(api: KalshiAPI, ticker: str) -> list[tuple[datetime, float]]:
    """(timestamp real, precio real 0-1) por cada trade de `ticker`,
    orden cronológico ascendente. Fail-closed: [] si no hay trades
    reales (mercado purgado o sin actividad)."""
    trades = api.get_trades(ticker=ticker, max_pages=10)
    parsed = []
    for t in trades:
        ts_raw = t.get("created_time")
        price_raw = t.get("yes_price_dollars")
        if ts_raw is None or price_raw is None:
            continue
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            price = float(price_raw)
        except (ValueError, TypeError):
            continue
        parsed.append((ts, price))
    parsed.sort(key=lambda x: x[0])  # más viejo primero
    return parsed


def _find_real_score(favorite_or_any_name: str, opponent_name: str, match_date: str | None) -> str | None:
    """Busca el score real set-por-set en el historial cacheado (DB-
    first, `recent_results_cache`) de `favorite_or_any_name` -- matchea
    por rival + fecha (misma tolerancia de +/-1 día que `find_match_id`).
    Fail-closed: None si no se encuentra."""
    if not match_date:
        return None
    rows = fetch_and_cache(favorite_or_any_name)
    if not rows:
        return None
    try:
        target_date = datetime.fromisoformat(match_date).date()
    except ValueError:
        return None
    for row in rows:
        if row.get("opponent") != opponent_name:
            continue
        row_date_raw = row.get("date")
        if not row_date_raw:
            continue
        try:
            row_date = datetime.fromisoformat(row_date_raw).date()
        except ValueError:
            continue
        if abs((row_date - target_date).days) <= 1:
            return row.get("score")
    return None


def backfill_match(api: KalshiAPI, entry: dict[str, Any]) -> dict[str, Any]:
    init_db_historical()
    market_id = entry["event_ticker"]
    report: dict[str, Any] = {"market_id": market_id, "steps": {}}

    # 1. Trade tape real de la pierna A -- fuente de verdad para
    # detected_at/settled_at Y para los ticks de probabilidad.
    ticker_a = entry.get("ticker_a")
    trades_a = _parse_trades(api, ticker_a) if ticker_a else []
    if not trades_a:
        report["steps"]["registration"] = "skipped: sin trades reales de ticker_a"
        return report

    detected_at = trades_a[0][0]
    settled_at = trades_a[-1][0]

    match = TennisScannedMatch.get_or_none(TennisScannedMatch.market_id == market_id)
    if match is None:
        winner_side = None
        if entry.get("winner") == entry.get("player_a"):
            winner_side = "a"
        elif entry.get("winner") == entry.get("player_b"):
            winner_side = "b"

        tour = "ATP" if entry["series"] == "KXATPMATCH" else "WTA"
        match = TennisScannedMatch.create(
            market_id=market_id, ticker_a=entry["ticker_a"], ticker_b=entry["ticker_b"],
            tour=tour, surface=None, tournament=entry.get("tournament"), round=entry.get("round"),
            player_a_name=entry["player_a"], player_b_name=entry["player_b"],
            player_a_rank_at_match=None, player_b_rank_at_match=None,
            best_of=3, detected_at=detected_at, winner=winner_side, settled_at=settled_at,
        )
        report["steps"]["registration"] = "created"
    else:
        report["steps"]["registration"] = "already existed"

    # 2. Ticks de probabilidad real (uno por trade real de la pierna A).
    existing_ticks = TennisMatchProbabilityTick.select().where(TennisMatchProbabilityTick.match == match).count()
    if existing_ticks == 0:
        for ts, price in trades_a:
            TennisMatchProbabilityTick.create(
                match=match, observed_at=ts, market_price_a=price, model_fair_a=None,
            )
        report["steps"]["ticks"] = f"created {len(trades_a)}"
    else:
        report["steps"]["ticks"] = f"already had {existing_ticks}"

    # 3. Stats reales de Flashscore (ya fetcheadas, cero costo) + score
    # real set-por-set (Tennis Abstract, cache DB-first).
    if not has_match_stats(market_id):
        stats = dict(entry.get("flashscore_stats") or {})
        score = _find_real_score(entry["player_a"], entry["player_b"], entry.get("match_date"))
        if score is None:
            score = _find_real_score(entry["player_b"], entry["player_a"], entry.get("match_date"))
        if score:
            stats["match_score"] = score
        if stats:
            save_match_stats(market_id, entry.get("flashscore_match_id") or "", stats)
            report["steps"]["stats"] = "saved" + (" (con score real)" if score else " (sin score real)")
        else:
            report["steps"]["stats"] = "skipped: sin stats ni score real"
    else:
        report["steps"]["stats"] = "already existed"

    # 4. Clima real (ya construido, ver match_weather.py) -- necesita
    # tournament reconocido en tournament_location.py, fail-closed si no.
    weather_result = populate_weather_for_match(match)
    report["steps"]["weather"] = weather_result

    return report


def run_backfill(dataset_path: str = "sf_final_scan_result.json") -> list[dict[str, Any]]:
    with open(dataset_path, encoding="utf-8") as f:
        entries = json.load(f)

    api = KalshiAPI()
    api.initialize()

    reports = []
    for entry in entries:
        try:
            reports.append(backfill_match(api, entry))
        except Exception as e:
            reports.append({"market_id": entry.get("event_ticker"), "error": str(e)})

    return reports


if __name__ == "__main__":
    results = run_backfill()
    summary = {
        "total": len(results),
        "errors": sum(1 for r in results if "error" in r),
        "created": sum(1 for r in results if r.get("steps", {}).get("registration") == "created"),
        "weather_populated": sum(
            1 for r in results
            if isinstance(r.get("steps", {}).get("weather"), dict)
            and r["steps"]["weather"].get("populated_phases")
        ),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    with open("sf_final_backfill_report.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
