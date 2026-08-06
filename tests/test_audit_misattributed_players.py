"""Tests de la auditoría+reparación de jugadores/as potencialmente mal
atribuidos (2026-08-06, bug real de `_verify_player_page` -- ver
`tennisexplorer_player_scraper.py` -- y el incidente real de la
primera versión de este script, que confundía fallo de red con
identidad incorrecta y purgó datos reales por error)."""

from __future__ import annotations

from unittest.mock import patch
from datetime import datetime, timezone

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis import TennisMatchHistory, TennisHistoricalOdds, WtaPlayerRankingHistory
from database.historical.tennis.tmldatabasematch import TmlDatabaseMatch
from scripts.tennis.audit_misattributed_players import find_at_risk_players, run_audit_and_repair, _verify_identity


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


def test_find_at_risk_players_detects_shared_surname_from_wide_universe():
    # a propósito NO depende de TennisMatchHistory -- reconstruye desde
    # fuentes que un purgado previo no puede alterar.
    TmlDatabaseMatch.create(
        year=2016, tourney_name="X", level="250", date="2016-01-01", round="F",
        winner_name="Casper Ruud", loser_name="Christian Ruud",
    )
    at_risk = find_at_risk_players()
    assert set(at_risk) == {"Casper Ruud", "Christian Ruud"}


def test_find_at_risk_players_excludes_players_without_collision():
    TmlDatabaseMatch.create(
        year=2016, tourney_name="X", level="250", date="2016-01-01", round="F",
        winner_name="Novak Djokovic", loser_name="Someone Else",
    )
    at_risk = find_at_risk_players()
    assert at_risk == []


def test_verify_identity_never_purges_on_network_failure():
    # 2026-08-06, EL bug real del incidente -- `_fetch` devolviendo
    # `None` (red caída) nunca debe interpretarse como "persona
    # incorrecta". Reintenta 3 veces (mismo criterio del script) y
    # devuelve "network_error", nunca "wrong_person".
    with patch(
        "scripts.tennis.audit_misattributed_players._fetch", return_value=None,
    ), patch("scripts.tennis.audit_misattributed_players.time.sleep"):
        result = _verify_identity("Casper Ruud")
    assert result == "network_error"


def test_verify_identity_confirms_real_match():
    with patch(
        "scripts.tennis.audit_misattributed_players._fetch", return_value="<html>x</html>",
    ), patch(
        "scripts.tennis.audit_misattributed_players._verify_player_page", return_value=True,
    ):
        result = _verify_identity("Casper Ruud")
    assert result == "confirmed"


def test_verify_identity_flags_wrong_person_only_when_fetch_succeeds():
    with patch(
        "scripts.tennis.audit_misattributed_players._fetch", return_value="<html>x</html>",
    ), patch(
        "scripts.tennis.audit_misattributed_players._verify_player_page", return_value=False,
    ):
        result = _verify_identity("Casper Ruud")
    assert result == "wrong_person"


def test_run_audit_and_repair_never_purges_on_network_failure():
    TmlDatabaseMatch.create(
        year=2016, tourney_name="X", level="250", date="2016-01-01", round="F",
        winner_name="Casper Ruud", loser_name="Christian Ruud",
    )
    TennisMatchHistory.create(
        player_name="Casper Ruud", opponent_name="X Y.", match_date="2016-01-01",
        result="W", score="6-4, 6-3", source="tennisexplorer", fetched_at=_now(),
    )

    with patch(
        "scripts.tennis.audit_misattributed_players._fetch", return_value=None,
    ), patch("scripts.tennis.audit_misattributed_players.time.sleep"):
        summary = run_audit_and_repair()

    assert summary["network_error_unresolved"] >= 1
    assert summary["wrong_person"] == 0
    # los datos existentes NUNCA se tocan ante fallo de red.
    assert TennisMatchHistory.select().where(TennisMatchHistory.player_name == "Casper Ruud").count() == 1


def test_run_audit_and_repair_purges_only_confirmed_wrong_identity():
    TmlDatabaseMatch.create(
        year=2016, tourney_name="X", level="250", date="2016-01-01", round="F",
        winner_name="Casper Ruud", loser_name="Christian Ruud",
    )
    TennisMatchHistory.create(
        player_name="Casper Ruud", opponent_name="X Y.", match_date="2016-01-01",
        result="W", score="6-4, 6-3", source="tennisexplorer", fetched_at=_now(),
    )
    TennisHistoricalOdds.create(
        player_name="Casper Ruud", opponent_name="X Y.", match_date="2016-01-01",
        odds_self=1.5, odds_opponent=2.5, source="tennisexplorer", fetched_at=_now(),
    )

    with patch(
        "scripts.tennis.audit_misattributed_players._fetch", return_value="<html>x</html>",
    ), patch(
        "scripts.tennis.audit_misattributed_players._verify_player_page",
        side_effect=lambda html, name: name != "Casper Ruud",  # Ruud confirma mal, Christian bien
    ), patch(
        # "Christian Ruud" confirma bien pero no tiene datos previos -- dispara
        # restauración real; se mockea para no pegarle a la red en el test.
        "scripts.tennis.audit_misattributed_players.backfill_player",
        return_value={"player": "Christian Ruud", "status": "ok", "matches_inserted": 0, "odds_inserted": 0},
    ):
        summary = run_audit_and_repair()

    assert "Casper Ruud" in summary["wrong_person_names"]
    assert TennisMatchHistory.select().where(TennisMatchHistory.player_name == "Casper Ruud").count() == 0
    assert TennisHistoricalOdds.select().where(TennisHistoricalOdds.player_name == "Casper Ruud").count() == 0


def test_run_audit_and_repair_restores_confirmed_player_with_no_existing_data():
    # simula exactamente el escenario del incidente: "Casper Ruud" fue
    # purgado antes (sin filas en TennisMatchHistory) pero SÍ es una
    # identidad real confirmable -- debe restaurarse vía backfill real,
    # no solo "confirmarse" sin hacer nada.
    TmlDatabaseMatch.create(
        year=2016, tourney_name="X", level="250", date="2016-01-01", round="F",
        winner_name="Casper Ruud", loser_name="Christian Ruud",
    )

    with patch(
        "scripts.tennis.audit_misattributed_players._fetch", return_value="<html>x</html>",
    ), patch(
        "scripts.tennis.audit_misattributed_players._verify_player_page", return_value=True,
    ), patch(
        "scripts.tennis.audit_misattributed_players.backfill_player",
        return_value={"player": "Casper Ruud", "status": "ok", "matches_inserted": 5, "odds_inserted": 3},
    ) as mock_backfill:
        summary = run_audit_and_repair()

    assert "Casper Ruud" in summary["restored_names"]
    assert "Christian Ruud" in summary["restored_names"]
    assert mock_backfill.call_count == 2
