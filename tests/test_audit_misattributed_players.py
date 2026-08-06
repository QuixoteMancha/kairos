"""Tests de la auditoría de jugadores/as potencialmente mal atribuidos
(2026-08-06, bug real de `_verify_player_page` -- ver
`tennisexplorer_player_scraper.py` y el módulo auditado)."""

from __future__ import annotations

from unittest.mock import patch
from datetime import datetime, timezone

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis import TennisMatchHistory, TennisHistoricalOdds, WtaPlayerRankingHistory
from database.historical.tennis.tmldatabasematch import TmlDatabaseMatch
from scripts.tennis.audit_misattributed_players import find_at_risk_players, run_audit


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        TennisMatchHistory.delete().execute()
        TennisHistoricalOdds.delete().execute()
        TmlDatabaseMatch.delete().execute()
        WtaPlayerRankingHistory.delete().execute()
    yield
    with db_historical.connection_context():
        TennisMatchHistory.delete().execute()
        TennisHistoricalOdds.delete().execute()
        TmlDatabaseMatch.delete().execute()
        WtaPlayerRankingHistory.delete().execute()


def _now():
    return datetime.now(timezone.utc)


def test_find_at_risk_players_detects_shared_surname():
    TennisMatchHistory.create(
        player_name="Casper Ruud", opponent_name="X Y.", match_date="2016-01-01",
        result="W", score="6-4, 6-3", source="tennisexplorer", fetched_at=_now(),
    )
    TmlDatabaseMatch.create(
        year=2016, tourney_name="X", level="250", date="2016-01-01", round="F",
        winner_name="Casper Ruud", loser_name="Christian Ruud",
    )
    at_risk = find_at_risk_players()
    assert at_risk == ["Casper Ruud"]


def test_find_at_risk_players_excludes_players_without_collision():
    TennisMatchHistory.create(
        player_name="Novak Djokovic", opponent_name="X Y.", match_date="2016-01-01",
        result="W", score="6-4, 6-3", source="tennisexplorer", fetched_at=_now(),
    )
    at_risk = find_at_risk_players()
    assert at_risk == []


def test_run_audit_purges_data_when_identity_not_confirmable():
    TennisMatchHistory.create(
        player_name="Casper Ruud", opponent_name="X Y.", match_date="2016-01-01",
        result="W", score="6-4, 6-3", source="tennisexplorer", fetched_at=_now(),
    )
    TennisHistoricalOdds.create(
        player_name="Casper Ruud", opponent_name="X Y.", match_date="2016-01-01",
        odds_self=1.5, odds_opponent=2.5, source="tennisexplorer", fetched_at=_now(),
    )
    TmlDatabaseMatch.create(
        year=2016, tourney_name="X", level="250", date="2016-01-01", round="F",
        winner_name="Casper Ruud", loser_name="Christian Ruud",
    )

    with patch(
        "scripts.tennis.audit_misattributed_players.guess_player_slug", return_value=None,
    ):
        summary = run_audit()

    assert summary == {"at_risk_total": 1, "confirmed": 0, "purged": 1, "purged_names": ["Casper Ruud"]}
    assert TennisMatchHistory.select().where(TennisMatchHistory.player_name == "Casper Ruud").count() == 0
    assert TennisHistoricalOdds.select().where(TennisHistoricalOdds.player_name == "Casper Ruud").count() == 0


def test_run_audit_keeps_data_when_identity_confirmed():
    TennisMatchHistory.create(
        player_name="Casper Ruud", opponent_name="X Y.", match_date="2016-01-01",
        result="W", score="6-4, 6-3", source="tennisexplorer", fetched_at=_now(),
    )
    TmlDatabaseMatch.create(
        year=2016, tourney_name="X", level="250", date="2016-01-01", round="F",
        winner_name="Casper Ruud", loser_name="Christian Ruud",
    )

    with patch(
        "scripts.tennis.audit_misattributed_players.guess_player_slug", return_value="ruud-real",
    ):
        summary = run_audit()

    assert summary["confirmed"] == 1
    assert summary["purged"] == 0
    assert TennisMatchHistory.select().where(TennisMatchHistory.player_name == "Casper Ruud").count() == 1
