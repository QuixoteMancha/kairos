"""Backfill de clima real para `MlbGameHistory` -- 2026-08-05, pedido
explícito del usuario ("adapta el backfill de clima de tenis para los
otros deportes que tiene el sistema").

Mismo patrón que `scripts/wta_match_history_weather_backfill.py`
(tenis): DB-first, solo procesa filas con `weather_fetched_at IS
NULL`, throttle real compartido de Open-Meteo. Requiere haber corrido
`scripts/mlb_local_backfill.py enrich_datetimes` primero (las filas
del backfill original de partidos no tienen `game_datetime_utc` -- ver
ese script)."""

from __future__ import annotations

from database.db import init_db_historical
from outer_apis.sports.baseball.match_weather import populate_weather_for_all_mlb_games

if __name__ == "__main__":
    init_db_historical()
    print(populate_weather_for_all_mlb_games())
