"""Tests del driver de reparación de score/cuota faltantes (2026-08-06,
pedido explícito del usuario -- "junta todos los matches sin score y
sin cuotas y arma un script que haga fill de ambos campos")."""

from __future__ import annotations

from unittest.mock import patch
from datetime import datetime, timezone

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis import TennisMatchHistory, TennisHistoricalOdds
from scripts.tennis.fill_missing_score_and_odds import find_players_missing_score_and_odds, run_fill


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        TennisMatchHistory.delete().execute()
        TennisHistoricalOdds.delete().execute()
    yield
    with db_historical.connection_context():
        TennisMatchHistory.delete().execute()
        TennisHistoricalOdds.delete().execute()


def _now():
    return datetime.now(timezone.utc)


def test_find_players_missing_score_and_odds_detects_empty_score():
    TennisMatchHistory.create(
        player_name="Elina Svitolina", opponent_name="Wang Xin.", match_date="2026-06-25",
        result="L", score="", source="flashscore", fetched_at=_now(),
    )
    players = find_players_missing_score_and_odds()
    assert players == ["Elina Svitolina"]


def test_find_players_missing_score_and_odds_excludes_rows_with_real_score():
    TennisMatchHistory.create(
        player_name="Elina Svitolina", opponent_name="Wang Xin.", match_date="2026-06-25",
        result="W", score="6-4, 6-2", source="tennisexplorer", fetched_at=_now(),
    )
    players = find_players_missing_score_and_odds()
    assert players == []


def test_find_players_missing_score_and_odds_excludes_rows_that_have_odds():
    # caso raro (0 medidos en vivo) pero fail-closed: si por algún
    # motivo ya hay cuota real asociada, no hace falta re-fetchear.
    TennisMatchHistory.create(
        player_name="Elina Svitolina", opponent_name="Wang Xin.", match_date="2026-06-25",
        result="L", score="", source="flashscore", fetched_at=_now(),
    )
    TennisHistoricalOdds.create(
        player_name="Elina Svitolina", opponent_name="Wang Xin.", match_date="2026-06-25",
        odds_self=1.5, odds_opponent=2.5, source="tennisexplorer", fetched_at=_now(),
    )
    players = find_players_missing_score_and_odds()
    assert players == []


def test_run_fill_calls_backfill_for_affected_players():
    TennisMatchHistory.create(
        player_name="Elina Svitolina", opponent_name="Wang Xin.", match_date="2026-06-25",
        result="L", score="", source="flashscore", fetched_at=_now(),
    )
    with patch(
        "scripts.tennis.fill_missing_score_and_odds._run_backfill_for_names",
    ) as mock_run:
        run_fill(max_workers=4)
    mock_run.assert_called_once_with(["Elina Svitolina"], tag="fill_missing_score_and_odds", max_workers=4)
