"""Tests del backfill de `tournament` para partidos registrados ANTES de
que esa columna existiera (2026-08-01, pedido explícito del usuario --
"deja un script para poblar la data climática de los matches ya
acontecidos")."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis.tennisscannedmatch import TennisScannedMatch
from database.historical.tennis.matchtracking import backfill_missing_tournaments


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        TennisScannedMatch.delete().execute()
    yield
    with db_historical.connection_context():
        TennisScannedMatch.delete().execute()


def _insert_old_match(market_id, tournament=None):
    return TennisScannedMatch.create(
        market_id=market_id, ticker_a=f"{market_id}-A", ticker_b=f"{market_id}-B",
        tour="ATP", surface="Hard", tournament=tournament,
        player_a_name="A", player_b_name="B", best_of=3,
        close_time="2026-08-01T00:00:00Z", detected_at=datetime.now(timezone.utc),
        winner="a", settled_at=datetime.now(timezone.utc),
    )


def test_backfill_recovers_tournament_from_real_ticker_lookup():
    _insert_old_match("OLD-1")
    _insert_old_match("OLD-2")

    fake_raw = {
        "OLD-1-A": {"rules_secondary": "...match in the 2026 ATP Washington Round Of 16 after a ball..."},
        "OLD-2-A": {"rules_secondary": "...match in the 2026 ATP Los Cabos Quarterfinal after a ball..."},
    }

    def fake_fetch(ticker):
        return fake_raw.get(ticker)

    result = backfill_missing_tournaments(fake_fetch)

    assert result == {"total_missing": 2, "updated": 2, "skipped": 0}
    m1 = TennisScannedMatch.get(TennisScannedMatch.market_id == "OLD-1")
    m2 = TennisScannedMatch.get(TennisScannedMatch.market_id == "OLD-2")
    assert m1.tournament == "Washington"
    assert m2.tournament == "Los Cabos"


def test_backfill_skips_when_ticker_purged_or_unparseable():
    _insert_old_match("OLD-3")

    result = backfill_missing_tournaments(lambda ticker: None)  # Kalshi ya no tiene el ticker

    assert result == {"total_missing": 1, "updated": 0, "skipped": 1}
    m3 = TennisScannedMatch.get(TennisScannedMatch.market_id == "OLD-3")
    assert m3.tournament is None


def test_backfill_ignores_matches_that_already_have_tournament():
    _insert_old_match("NEW-1", tournament="Wimbledon")

    result = backfill_missing_tournaments(lambda ticker: {"rules_secondary": "should never be called"})

    assert result == {"total_missing": 0, "updated": 0, "skipped": 0}
