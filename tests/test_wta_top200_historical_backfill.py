"""Tests del backfill masivo top-200 WTA (2026-08-03, pedido explícito
del usuario). Mockea red (rankings + fetch de TennisExplorer) y usa la
DB histórica real (sqlite en memoria/test) -- nunca golpea la red en
tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis.tennismatchhistory import TennisMatchHistory
from database.historical.tennis.tennishistoricalodds import TennisHistoricalOdds
from scripts.wta_top200_historical_backfill import backfill_player, run_backfill, _to_iso_date


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        TennisMatchHistory.delete().where(TennisMatchHistory.source == "tennisexplorer").execute()
        TennisHistoricalOdds.delete().execute()
    yield
    with db_historical.connection_context():
        TennisMatchHistory.delete().where(TennisMatchHistory.source == "tennisexplorer").execute()
        TennisHistoricalOdds.delete().execute()


def _fake_html(marker: str) -> str:
    return f"<title>Test Player - Tennis Explorer</title>{marker}"


def test_to_iso_date_uses_reference_year():
    assert _to_iso_date("03.08.", 2025) == "2025-08-03"


def test_to_iso_date_fail_closed_on_garbage():
    assert _to_iso_date("garbage", 2025) is None


def test_backfill_player_skips_when_no_slug():
    with patch(
        "scripts.wta_top200_historical_backfill.guess_player_slug", return_value=None,
    ):
        result = backfill_player("Nonexistent Player")
    assert result["status"] == "skipped_no_slug"
    assert result["matches_inserted"] == 0


def test_backfill_player_inserts_match_and_odds():
    fake_match = {
        "date": "03.08.", "tournament": "Washington", "surface": "hard",
        "opponent": "Pegula J.", "round": "F", "round_full": "final",
        "score": "4-6, 6-4, 6-0", "odds_self": 2.36, "odds_opponent": 1.58,
    }

    def fake_fetch(slug, year=None):
        return f"year={year}"  # marca el año en el html devuelto, para el side_effect de abajo

    def fake_parse(html, name):
        # solo un año real trae este partido -- los otros 10 vienen vacíos
        # (fail-closed, como pasaría de verdad con años sin cobertura).
        return [fake_match] if html == "year=2026" else []

    with patch(
        "scripts.wta_top200_historical_backfill.guess_player_slug", return_value="testplayer",
    ), patch(
        "scripts.wta_top200_historical_backfill._fetch", side_effect=fake_fetch,
    ), patch(
        "scripts.wta_top200_historical_backfill._parse_year_html", side_effect=fake_parse,
    ), patch(
        "scripts.wta_top200_historical_backfill.time.sleep",
    ):
        result = backfill_player("Test Player")

    assert result["status"] == "ok"
    assert result["matches_inserted"] == 1
    assert result["odds_inserted"] == 1

    with db_historical.connection_context():
        assert TennisMatchHistory.select().where(TennisMatchHistory.player_name == "Test Player").count() == 1
        row = TennisMatchHistory.get(TennisMatchHistory.player_name == "Test Player")
        assert row.result == "W"
        assert row.tournament == "Washington"
        assert row.round == "F"
        assert row.surface == "hard"

        odds_row = TennisHistoricalOdds.get(TennisHistoricalOdds.player_name == "Test Player")
        assert odds_row.odds_self == 2.36
        assert odds_row.odds_opponent == 1.58


def test_backfill_player_idempotent_on_second_run():
    fake_match = {
        "date": "03.08.", "tournament": "Washington", "surface": "hard",
        "opponent": "Pegula J.", "round": "F", "round_full": "final",
        "score": "4-6, 6-4, 6-0", "odds_self": 2.36, "odds_opponent": 1.58,
    }
    with patch(
        "scripts.wta_top200_historical_backfill.guess_player_slug", return_value="testplayer",
    ), patch(
        "scripts.wta_top200_historical_backfill._fetch", return_value="<html>x</html>",
    ), patch(
        # mismo partido reportado en LOS 11 años pedidos -> misma fecha real
        # calculada distinta por año (2016-08-03 ... 2026-08-03) NO
        # colapsa entre sí -- lo que sí debe colapsar es correr la
        # función 2 VECES con el mismo input (esto es lo que se testea).
        "scripts.wta_top200_historical_backfill._parse_year_html", return_value=[fake_match],
    ), patch(
        "scripts.wta_top200_historical_backfill.time.sleep",
    ):
        first = backfill_player("Test Player")
        second = backfill_player("Test Player")

    assert second["matches_inserted"] == 0
    assert second["odds_inserted"] == 0
    with db_historical.connection_context():
        assert TennisMatchHistory.select().where(
            TennisMatchHistory.player_name == "Test Player"
        ).count() == first["matches_inserted"]


def test_backfill_player_skips_match_without_valid_date():
    fake_match = {
        "date": "garbage", "tournament": "X", "surface": "hard",
        "opponent": "Y", "round": "F", "round_full": "final",
        "score": "6-4, 6-3", "odds_self": None, "odds_opponent": None,
    }
    with patch(
        "scripts.wta_top200_historical_backfill.guess_player_slug", return_value="testplayer",
    ), patch(
        "scripts.wta_top200_historical_backfill._fetch", return_value="<html>x</html>",
    ), patch(
        "scripts.wta_top200_historical_backfill._parse_year_html", return_value=[fake_match],
    ), patch(
        "scripts.wta_top200_historical_backfill.time.sleep",
    ):
        result = backfill_player("Test Player")
    assert result["matches_inserted"] == 0


def test_backfill_player_no_odds_row_when_both_odds_none():
    fake_match = {
        "date": "03.08.", "tournament": "X", "surface": "hard",
        "opponent": "Y", "round": "F", "round_full": "final",
        "score": "6-4, 6-3", "odds_self": None, "odds_opponent": None,
    }
    with patch(
        "scripts.wta_top200_historical_backfill.guess_player_slug", return_value="testplayer",
    ), patch(
        "scripts.wta_top200_historical_backfill._fetch", return_value="<html>x</html>",
    ), patch(
        "scripts.wta_top200_historical_backfill._parse_year_html", return_value=[fake_match],
    ), patch(
        "scripts.wta_top200_historical_backfill.time.sleep",
    ):
        result = backfill_player("Test Player")
    assert result["odds_inserted"] == 0


def test_run_backfill_paces_between_players_and_summarizes():
    fake_players = [
        {"nombre": "Player A", "ranking": 1, "ranking_type": "WTA", "pais": "USA"},
        {"nombre": "Player B", "ranking": 2, "ranking_type": "WTA", "pais": "USA"},
    ]
    with patch(
        "scripts.wta_top200_historical_backfill.get_top_rankings", return_value=fake_players,
    ), patch(
        "scripts.wta_top200_historical_backfill.backfill_player",
        return_value={"player": "x", "status": "ok", "matches_inserted": 1, "odds_inserted": 1},
    ) as mock_bp, patch(
        "scripts.wta_top200_historical_backfill.time.sleep",
    ) as mock_sleep:
        summary = run_backfill(n_players=2)

    assert mock_bp.call_count == 2
    assert mock_sleep.call_count == 2  # una pausa entre jugadoras por cada jugadora procesada
    assert len(summary) == 2


def test_run_backfill_uses_requested_tour_for_rankings():
    fake_players = [{"nombre": "Player A", "ranking": 1, "ranking_type": "ATP", "pais": "ESP"}]
    with patch(
        "scripts.wta_top200_historical_backfill.get_top_rankings", return_value=fake_players,
    ) as mock_rankings, patch(
        "scripts.wta_top200_historical_backfill.backfill_player",
        return_value={"player": "x", "status": "ok", "matches_inserted": 0, "odds_inserted": 0},
    ), patch(
        "scripts.wta_top200_historical_backfill.time.sleep",
    ):
        run_backfill(n_players=1, tour="ATP")
    mock_rankings.assert_called_once_with(n=1, tour="ATP")
