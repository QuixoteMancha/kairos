"""Puebla clima real (temperatura/humedad/viento, Open-Meteo) para todos
los partidos de tenis ya resueltos en `TennisScannedMatch` -- pedido
explícito del usuario 2026-08-01, primer paso hacia clasificar pérdidas
de jugadores top-100 por temperatura/humedad.

Backfill de `tournament` incluido (mismo pedido, "de todas formas"):
partidos registrados ANTES de que esa columna existiera (ej. Osaka/
Cocciaretto, Tabilo/Shelton) no tienen torneo guardado y por lo tanto
quedarían afuera del poblado de clima para siempre -- este script
primero intenta recuperar el torneo re-consultando Kalshi por el ticker
ya guardado (`backfill_missing_tournaments`), y RECIÉN DESPUÉS puebla el
clima, para que esos partidos viejos también queden cubiertos cuando sea
posible. Fail-closed por partido: si Kalshi ya purgó el ticker o no
matchea el patrón esperado, ese partido queda sin torneo (se reintenta
en la próxima corrida, nunca se adivina).

Uso:
    python scripts/populate_match_weather.py          # corre una vez
    python scripts/populate_match_weather.py --watch  # loop cada N min (ENV o default 30)
"""

import argparse
import os
import time

from outer_apis.kalshi.client import KalshiAPI
from database.historical.tennis.matchtracking import backfill_missing_tournaments
from outer_apis.sports.tennis.match_weather import populate_weather_for_all_resolved_matches

_DEFAULT_INTERVAL_MIN = 30


def _fetch_raw_market(ticker: str) -> dict | None:
    api = KalshiAPI()
    api.initialize()
    result = api.get_markets_by_tickers([ticker])
    market = result.get(ticker)
    return market.get("raw") if market else None


def _run_once():
    backfill_result = backfill_missing_tournaments(_fetch_raw_market)
    print(f"[populate_match_weather] backfill de torneos: {backfill_result}")
    result = populate_weather_for_all_resolved_matches()
    print(f"[populate_match_weather] {result}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", action="store_true", help="Loop continuo")
    parser.add_argument("--interval", type=int, default=int(os.getenv("WEATHER_POPULATE_INTERVAL_MINUTES", _DEFAULT_INTERVAL_MIN)))
    args = parser.parse_args()

    if not args.watch:
        _run_once()
        return

    print(f"[populate_match_weather] modo watch, cada {args.interval} min")
    while True:
        try:
            _run_once()
        except Exception as e:
            print(f"[populate_match_weather] error: {e}")
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
