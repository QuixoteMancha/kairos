"""Exporta a JSON las tablas que esta sesión de investigación pobló en
la DB LOCAL (marrakesh) para migrarlas a la DB real de producción en la
mini-pc (`/data_historical/kairos.db`, volumen `kairos_kairos_data`) --
2026-08-04, pedido explícito del usuario tras notar que toda esta
investigación había quedado en la DB local en vez de en el volumen real
("recuerda que la base está en la mini-pc no acá").

Herramienta de migración de UNA VEZ, no un componente de producción --
se corre acá (local) para generar los JSON, después se copian a la
mini-pc y se importan con `scripts/import_tennis_data_from_json.py`
(corrido DENTRO del contenedor `kairos`)."""

from __future__ import annotations

import json

from database.db import init_db_historical
from database.historical.tennis import TmlDatabaseMatch, TennisHistoricalOdds, TennisMatchHistory


def _model_to_dict(row, fields: list[str]) -> dict:
    out = {}
    for f in fields:
        v = getattr(row, f)
        if hasattr(v, "isoformat"):  # datetime
            v = v.isoformat()
        out[f] = v
    return out


def export_all(output_dir: str = "/tmp") -> dict[str, int]:
    init_db_historical()

    tml_fields = [
        "year", "tourney_name", "level", "date", "round", "surface", "best_of",
        "winner_name", "winner_rank", "winner_age", "loser_name", "loser_rank",
        "loser_age", "score", "minutes",
    ]
    odds_fields = ["player_name", "opponent_name", "match_date", "odds_self", "odds_opponent", "source", "fetched_at"]
    history_fields = [
        "player_name", "opponent_name", "match_date", "result", "score", "source", "fetched_at",
        "tournament", "round", "surface", "temperature_c", "humidity_pct", "wind_kmh", "weather_fetched_at",
    ]

    counts = {}
    for name, model, fields in [
        ("tml_database_match", TmlDatabaseMatch, tml_fields),
        ("tennis_historical_odds", TennisHistoricalOdds, odds_fields),
        ("tennis_match_history", TennisMatchHistory, history_fields),
    ]:
        rows = [_model_to_dict(r, fields) for r in model.select()]
        path = f"{output_dir}/{name}_export.json"
        with open(path, "w") as f:
            json.dump(rows, f)
        counts[name] = len(rows)
        print(f"{name}: {len(rows)} filas -> {path}")

    return counts


if __name__ == "__main__":
    export_all()
