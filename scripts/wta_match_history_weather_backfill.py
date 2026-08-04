"""Puebla el clima (snapshot único, ver docstring de
`outer_apis/sports/tennis/match_weather.py::populate_weather_for_match_history_row`)
de todas las filas de `TennisMatchHistory` que todavía no lo tienen --
pedido explícito del usuario 2026-08-03, fase final del backfill masivo
top-200 WTA (`scripts/wta_top200_historical_backfill.py`).

Correr DESPUÉS de que termine ese backfill (necesita que las filas ya
existan) -- no tiene una dependencia de código, pero corrió como
segunda fase intencional en la misma sesión."""

from __future__ import annotations

import json

from database.db import init_db_historical
from outer_apis.sports.tennis.match_weather import populate_weather_for_all_match_history

if __name__ == "__main__":
    init_db_historical()
    result = populate_weather_for_all_match_history()
    print(json.dumps(result, indent=2, ensure_ascii=False))
