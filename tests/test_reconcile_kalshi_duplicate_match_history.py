from datetime import datetime, timezone

import pytest

from database.db import init_db_historical
from database.historical.tennis.models import Match, MatchHistory, MatchStats, Player
from scripts.tennis.reconcile_kalshi_duplicate_match_history import (
    reconcile_kalshi_duplicate_match_history,
)


@pytest.fixture(autouse=True)
def clean_tables():
    init_db_historical()
    models = (MatchStats, Match, MatchHistory, Player)
    for model in models:
        model.delete().execute()
    yield
    for model in models:
        model.delete().execute()


def _player(name):
    return Player.create(canonical_name=name, created_at=datetime.now(timezone.utc))


def test_merges_synthetic_kalshi_row_with_real_row_by_ticker_date():
    """2026-08-28, bug real: `close_time` de KXWTAMATCH puede ser la
    expiración de la serie (07-SEP), no la fecha real del ticker
    (17-AUG) -- el duplicado sintético quedó creado con `match_date`
    equivocada. La reconciliación debe recalcular la fecha REAL del
    ticker (no confiar en `synthetic.match_date`, que está mal) y
    encontrar el `MatchHistory` real de TennisExplorer para esa fecha."""
    a = _player("Ticker Real")
    b = _player("Ticker Rival")
    synthetic = MatchHistory.create(
        match_id="kalshi:KXWTAMATCH-26AUG17TJEAND", source="kalshi", tour="WTA",
        match_date="2026-08-31",  # fecha equivocada, calculada de close_time
        player_a=a, player_b=b, winner=None,
        fetched_at=datetime.now(timezone.utc),
    )
    canonical = MatchHistory.create(
        match_id="te:real-3294705", source="tennisexplorer", tour="WTA",
        match_date="2026-08-17", tournament=None, surface="Hard",
        player_a=a, player_b=b, winner=a.id,
        fetched_at=datetime.now(timezone.utc),
    )
    scanned = Match.create(
        market_id="KXWTAMATCH-26AUG17TJEAND", ticker_a="A", ticker_b="B",
        tour="WTA", player_a_name="Ticker Real", player_b_name="Ticker Rival",
        best_of=3, close_time="2026-08-31T15:00:00Z",
        detected_at=datetime.now(timezone.utc), match_history=synthetic,
    )
    MatchStats.create(
        match_history=synthetic, source="flashscore", raw_stats_json="{}",
        fetched_at=datetime.now(timezone.utc),
    )

    dry_run = reconcile_kalshi_duplicate_match_history()
    assert dry_run["matched"] == 1
    assert dry_run["deleted"] == 0
    assert MatchHistory.select().count() == 2

    applied = reconcile_kalshi_duplicate_match_history(apply=True)
    assert applied["deleted"] == 1
    assert applied["dependencies_moved"] == 1  # el MatchStats se movió
    assert MatchHistory.select().count() == 1
    assert MatchHistory.get().id == canonical.id

    scanned = Match.get(Match.id == scanned.id)
    assert scanned.match_history_id == canonical.id
    assert MatchStats.get().match_history_id == canonical.id


def test_leaves_synthetic_row_alone_when_no_real_candidate_exists_yet():
    """El caso más común de los 108 reales: partido recién jugado, aún no
    scrapeado de TennisExplorer -- no hay candidato real todavía, no se
    debe borrar ni adivinar."""
    a = _player("Solo Kalshi A")
    b = _player("Solo Kalshi B")
    synthetic = MatchHistory.create(
        match_id="kalshi:KXATPMATCH-26AUG27ABCDEF", source="kalshi", tour="ATP",
        match_date="2026-09-10", player_a=a, player_b=b,
        fetched_at=datetime.now(timezone.utc),
    )
    Match.create(
        market_id="KXATPMATCH-26AUG27ABCDEF", ticker_a="A", ticker_b="B",
        tour="ATP", player_a_name="Solo Kalshi A", player_b_name="Solo Kalshi B",
        best_of=3, close_time="2026-09-10T15:00:00Z",
        detected_at=datetime.now(timezone.utc), match_history=synthetic,
    )

    result = reconcile_kalshi_duplicate_match_history(apply=True)
    assert result["no_candidate_yet"] == 1
    assert result["deleted"] == 0
    assert MatchHistory.select().count() == 1


def test_leaves_synthetic_row_alone_when_multiple_candidates_ambiguous():
    a = _player("Ambiguous A")
    b = _player("Ambiguous B")
    synthetic = MatchHistory.create(
        match_id="kalshi:KXATPMATCH-26AUG17GHIJKL", source="kalshi", tour="ATP",
        match_date="2026-08-31", player_a=a, player_b=b,
        fetched_at=datetime.now(timezone.utc),
    )
    Match.create(
        market_id="KXATPMATCH-26AUG17GHIJKL", ticker_a="A", ticker_b="B",
        tour="ATP", player_a_name="Ambiguous A", player_b_name="Ambiguous B",
        best_of=3, close_time="2026-08-31T15:00:00Z",
        detected_at=datetime.now(timezone.utc), match_history=synthetic,
    )
    MatchHistory.create(
        match_id="te:dup-1", source="tennisexplorer", tour="ATP",
        match_date="2026-08-17", player_a=a, player_b=b,
        fetched_at=datetime.now(timezone.utc),
    )
    MatchHistory.create(
        match_id="te:dup-2", source="tennisexplorer", tour="ATP",
        match_date="2026-08-17", player_a=a, player_b=b,
        fetched_at=datetime.now(timezone.utc),
    )

    result = reconcile_kalshi_duplicate_match_history(apply=True)
    assert result["ambiguous"] == 1
    assert result["deleted"] == 0
    assert MatchHistory.select().count() == 3
