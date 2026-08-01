"""Puebla clima real (temperatura/humedad/viento, Open-Meteo) para todos
los partidos de tenis ya resueltos en `TennisScannedMatch` -- pedido
explícito del usuario 2026-08-01, primer paso hacia clasificar pérdidas
de jugadores top-100 por temperatura/humedad.

Uso:
    python scripts/populate_match_weather.py          # corre una vez
    python scripts/populate_match_weather.py --watch  # loop cada N min (ENV o default 30)
"""

import argparse
import os
import time

from outer_apis.sports.tennis.match_weather import populate_weather_for_all_resolved_matches

_DEFAULT_INTERVAL_MIN = 30


def _run_once():
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
