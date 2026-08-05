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
from scripts.tennis.wta_top200_historical_backfill import (
    backfill_player, run_backfill, run_backfill_from_wta_roster, _to_iso_date,
)
from database.historical.tennis import WtaPlayerRankingHistory


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
        "scripts.tennis.wta_top200_historical_backfill.guess_player_slug", return_value=None,
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
        "scripts.tennis.wta_top200_historical_backfill.guess_player_slug", return_value="testplayer",
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill._fetch", side_effect=fake_fetch,
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill._parse_year_html", side_effect=fake_parse,
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill.time.sleep",
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
        "scripts.tennis.wta_top200_historical_backfill.guess_player_slug", return_value="testplayer",
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill._fetch", return_value="<html>x</html>",
    ), patch(
        # mismo partido reportado en LOS 11 años pedidos -> misma fecha real
        # calculada distinta por año (2016-08-03 ... 2026-08-03) NO
        # colapsa entre sí -- lo que sí debe colapsar es correr la
        # función 2 VECES con el mismo input (esto es lo que se testea).
        "scripts.tennis.wta_top200_historical_backfill._parse_year_html", return_value=[fake_match],
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill.time.sleep",
    ):
        first = backfill_player("Test Player")
        second = backfill_player("Test Player")

    assert second["matches_inserted"] == 0
    assert second["odds_inserted"] == 0
    with db_historical.connection_context():
        assert TennisMatchHistory.select().where(
            TennisMatchHistory.player_name == "Test Player"
        ).count() == first["matches_inserted"]


def test_backfill_player_repairs_existing_row_with_null_tournament():
    """2026-08-04, pedido explícito del usuario -- reparar filas que
    quedaron con tournament=NULL por el bug real de `type-women2`
    hardcodeado en el scraper (ya corregido). Antes de este fix, el
    chequeo de existencia trataba una fila con tournament=NULL como
    "ya migrada" y la saltaba para siempre, sin repararla nunca."""
    with db_historical.connection_context():
        from datetime import datetime, timezone
        TennisMatchHistory.create(
            player_name="Test Player", opponent_name="Pegula J.", match_date="2026-08-03",
            result="W", score="4-6, 6-4, 6-0", source="tennisexplorer",
            tournament=None, round=None, surface=None,  # simula el bug real ya guardado
            fetched_at=datetime.now(timezone.utc),
        )

    fake_match = {
        "date": "03.08.", "tournament": "Washington", "surface": "hard",
        "opponent": "Pegula J.", "round": "F", "round_full": "final",
        "score": "4-6, 6-4, 6-0", "odds_self": None, "odds_opponent": None,
    }

    def fake_fetch(slug, year=None):
        return f"year={year}"

    def fake_parse(html, name):
        # solo el año 2026 trae este partido -- coincide con la fila
        # pre-existente (2026-08-03); los otros 10 años vienen vacíos.
        return [fake_match] if html == "year=2026" else []

    with patch(
        "scripts.tennis.wta_top200_historical_backfill.guess_player_slug", return_value="testplayer",
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill._fetch", side_effect=fake_fetch,
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill._parse_year_html", side_effect=fake_parse,
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill.time.sleep",
    ):
        result = backfill_player("Test Player")

    assert result["matches_inserted"] == 0  # la fila YA existía, no se duplica
    assert result["matches_repaired"] == 1
    with db_historical.connection_context():
        row = TennisMatchHistory.get(TennisMatchHistory.player_name == "Test Player")
        assert row.tournament == "Washington"
        assert row.round == "F"
        assert row.surface == "hard"


def test_backfill_player_skips_match_without_valid_date():
    fake_match = {
        "date": "garbage", "tournament": "X", "surface": "hard",
        "opponent": "Y", "round": "F", "round_full": "final",
        "score": "6-4, 6-3", "odds_self": None, "odds_opponent": None,
    }
    with patch(
        "scripts.tennis.wta_top200_historical_backfill.guess_player_slug", return_value="testplayer",
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill._fetch", return_value="<html>x</html>",
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill._parse_year_html", return_value=[fake_match],
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill.time.sleep",
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
        "scripts.tennis.wta_top200_historical_backfill.guess_player_slug", return_value="testplayer",
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill._fetch", return_value="<html>x</html>",
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill._parse_year_html", return_value=[fake_match],
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill.time.sleep",
    ):
        result = backfill_player("Test Player")
    assert result["odds_inserted"] == 0


def test_run_backfill_paces_between_players_and_summarizes():
    fake_players = [
        {"nombre": "Player A", "ranking": 1, "ranking_type": "WTA", "pais": "USA"},
        {"nombre": "Player B", "ranking": 2, "ranking_type": "WTA", "pais": "USA"},
    ]
    with patch(
        "scripts.tennis.wta_top200_historical_backfill.get_top_rankings", return_value=fake_players,
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill.backfill_player",
        return_value={"player": "x", "status": "ok", "matches_inserted": 1, "odds_inserted": 1},
    ) as mock_bp, patch(
        "scripts.tennis.wta_top200_historical_backfill.time.sleep",
    ) as mock_sleep:
        summary = run_backfill(n_players=2)

    assert mock_bp.call_count == 2
    assert mock_sleep.call_count == 2  # una pausa entre jugadoras por cada jugadora procesada
    assert len(summary) == 2


def test_run_backfill_uses_requested_tour_for_rankings():
    fake_players = [{"nombre": "Player A", "ranking": 1, "ranking_type": "ATP", "pais": "ESP"}]
    with patch(
        "scripts.tennis.wta_top200_historical_backfill.get_top_rankings", return_value=fake_players,
    ) as mock_rankings, patch(
        "scripts.tennis.wta_top200_historical_backfill.backfill_player",
        return_value={"player": "x", "status": "ok", "matches_inserted": 0, "odds_inserted": 0},
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill.time.sleep",
    ):
        run_backfill(n_players=1, tour="ATP")
    mock_rankings.assert_called_once_with(n=1, tour="ATP")


def test_run_backfill_from_wta_roster_uses_distinct_names_from_ranking_table():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    WtaPlayerRankingHistory.delete().execute()
    WtaPlayerRankingHistory.create(player_id=1, player_name="Player A", ranked_at="2020-01-01", singles_ranking=5, fetched_at=now)
    WtaPlayerRankingHistory.create(player_id=1, player_name="Player A", ranked_at="2021-01-01", singles_ranking=3, fetched_at=now)
    WtaPlayerRankingHistory.create(player_id=2, player_name="Player B", ranked_at="2020-01-01", singles_ranking=50, fetched_at=now)

    with patch(
        "scripts.tennis.wta_top200_historical_backfill.backfill_player",
        return_value={"player": "x", "status": "ok", "matches_inserted": 0, "odds_inserted": 0},
    ) as mock_bp, patch("scripts.tennis.wta_top200_historical_backfill.time.sleep"):
        summary = run_backfill_from_wta_roster()

    # 2 jugadoras DISTINTAS (Player A aparece 2 veces en la tabla, 1 sola vez en el roster)
    assert mock_bp.call_count == 2
    assert len(summary) == 2
