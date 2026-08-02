"""Puebla el clima (snapshot único, ver docstring de
`outer_apis/sports/tennis/match_weather.py::populate_weather_for_charted_match`)
de todos los `ChartedMatch` (Match Charting Project) que todavía no lo
tienen -- pedido explícito del usuario 2026-08-02."""

from __future__ import annotations

import json

from database.db import init_db_historical
from outer_apis.sports.tennis.match_weather import populate_weather_for_all_charted_matches

if __name__ == "__main__":
    init_db_historical()
    result = populate_weather_for_all_charted_matches()
    print(json.dumps(result, indent=2, ensure_ascii=False))
