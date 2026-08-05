"""Tests de la migración de datos de tenis local -> mini-pc (2026-08-04,
pedido explícito del usuario tras notar que la investigación de la
sesión había quedado en la DB local en vez del volumen real de
producción)."""

from __future__ import annotations

import json

from database.db import init_db_historical
from database.historical.tennis import TmlDatabaseMatch, TennisHistoricalOdds, TennisMatchHistory
from scripts.tennis.export_local_tennis_data_for_minipc_migration import export_all
from scripts.tennis.import_tennis_data_from_json import (
    import_tml_database_matches, import_historical_odds, import_match_history,
)


def setup_function(_fn):
    init_db_historical()
    TmlDatabaseMatch.delete().execute()
    TennisHistoricalOdds.delete().execute()
    TennisMatchHistory.delete().execute()


def test_export_all_writes_json_files_with_correct_counts(tmp_path):
    TmlDatabaseMatch.create(
        year=2020, tourney_name="X", level="M", date="2020-01-01", round="F",
        surface="Hard", best_of=3, winner_name="A", winner_rank=5, winner_age=25.0,
        loser_name="B", loser_rank=50, loser_age=25.0, score="6-4 6-3", minutes=95,
    )
    counts = export_all(output_dir=str(tmp_path))
    assert counts["tml_database_match"] == 1
    assert (tmp_path / "tml_database_match_export.json").exists()
    with open(tmp_path / "tml_database_match_export.json") as f:
        data = json.load(f)
    assert data[0]["winner_name"] == "A"


def test_import_tml_database_matches_inserts_rows(tmp_path):
    rows = [{
        "year": 2020, "tourney_name": "X", "level": "M", "date": "2020-01-01", "round": "F",
        "surface": "Hard", "best_of": 3, "winner_name": "A", "winner_rank": 5, "winner_age": 25.0,
        "loser_name": "B", "loser_rank": 50, "loser_age": 25.0, "score": "6-4 6-3", "minutes": 95,
    }]
    path = tmp_path / "tml_database_match_export.json"
    with open(path, "w") as f:
        json.dump(rows, f)
    n = import_tml_database_matches(str(path))
    assert n == 1
    assert TmlDatabaseMatch.select().count() == 1


def test_import_historical_odds_parses_fetched_at(tmp_path):
    rows = [{
        "player_name": "A", "opponent_name": "B", "match_date": "2020-01-01",
        "odds_self": 1.5, "odds_opponent": 2.5, "source": "tennisexplorer",
        "fetched_at": "2026-08-01T00:00:00",
    }]
    path = tmp_path / "tennis_historical_odds_export.json"
    with open(path, "w") as f:
        json.dump(rows, f)
    n = import_historical_odds(str(path))
    assert n == 1
    row = TennisHistoricalOdds.get()
    assert row.fetched_at is not None


def test_import_match_history_deduplicates_against_existing_rows(tmp_path):
    # fila YA existente en la DB destino (simula los 10,216 reales de producción)
    TennisMatchHistory.create(
        player_name="A", opponent_name="B", match_date="2020-01-01", result="W",
        score="6-4", source="flashscore", fetched_at="2026-01-01T00:00:00",
    )
    rows = [
        {  # duplicado exacto -- debe saltarse
            "player_name": "A", "opponent_name": "B", "match_date": "2020-01-01", "result": "W",
            "score": "6-4", "source": "flashscore", "fetched_at": "2026-01-01T00:00:00",
            "tournament": None, "round": None, "surface": None,
            "temperature_c": None, "humidity_pct": None, "wind_kmh": None, "weather_fetched_at": None,
        },
        {  # nueva -- debe insertarse
            "player_name": "C", "opponent_name": "D", "match_date": "2021-01-01", "result": "L",
            "score": "3-6", "source": "tennisexplorer", "fetched_at": "2026-08-01T00:00:00",
            "tournament": "Wimbledon", "round": "F", "surface": "Grass",
            "temperature_c": 20.0, "humidity_pct": 50.0, "wind_kmh": 10.0, "weather_fetched_at": None,
        },
    ]
    path = tmp_path / "tennis_match_history_export.json"
    with open(path, "w") as f:
        json.dump(rows, f)
    result = import_match_history(str(path))
    assert result["total_in_export"] == 2
    assert result["skipped_already_existed"] == 1
    assert result["inserted"] == 1
    assert TennisMatchHistory.select().count() == 2
