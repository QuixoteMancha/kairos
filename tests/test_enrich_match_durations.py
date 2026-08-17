"""Tests de `scripts/tennis/enrich_match_durations.py` -- 2026-08-12,
pedido explícito del usuario ("un enrich pipeline posterior con la
duración del match y de los sets"). Mockea `find_match_id`/
`get_match_duration` (ya tienen su propia cobertura real) -- estos
tests verifican la ORQUESTACIÓN: qué partidos se seleccionan, idempotencia,
fail-closed por partido."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis import MatchHistory, MatchDuration, Player
from database.historical.tennis.models import MatchStats
from scripts.tennis.enrich_match_durations import enrich_match_durations
import scripts.tennis.enrich_match_durations as durations_module
from database.historical.workerstate import HistoricalEnrichmentAttempt

_next_id = iter(range(1, 100_000))


@pytest.fixture(autouse=True)
def clean_db():
    durations_module._batch_cursor_id = 0
    init_db_historical()
    with db_historical.connection_context():
        HistoricalEnrichmentAttempt.delete().where(
            HistoricalEnrichmentAttempt.job_name == "tennis_match_durations"
        ).execute()
        MatchStats.delete().execute()
        MatchHistory.delete().execute()
        MatchDuration.delete().execute()
        Player.delete().execute()
    yield
    durations_module._batch_cursor_id = 0
    with db_historical.connection_context():
        HistoricalEnrichmentAttempt.delete().where(
            HistoricalEnrichmentAttempt.job_name == "tennis_match_durations"
        ).execute()
        MatchStats.delete().execute()
        MatchHistory.delete().execute()
        MatchDuration.delete().execute()
        Player.delete().execute()


def _make_match(**overrides):
    a = Player.create(canonical_name=f"A{next(_next_id)}", created_at=datetime.now(timezone.utc))
    b = Player.create(canonical_name=f"B{next(_next_id)}", created_at=datetime.now(timezone.utc))
    base = dict(
        match_id=f"test:{next(_next_id)}", source="tennisexplorer", match_date="2025-01-01",
        player_a=a, player_b=b, score="6-4, 6-3", fetched_at=datetime.now(timezone.utc),
    )
    base.update(overrides)
    return MatchHistory.create(**base)


_DURATION = {"total_minutes": 100, "set_1_minutes": 50, "set_2_minutes": 50, "set_3_minutes": None, "set_4_minutes": None, "set_5_minutes": None}


def test_resolves_duration_for_eligible_match():
    match = _make_match()
    with patch(
        "scripts.tennis.enrich_match_durations.find_match_id", return_value="fs123",
    ), patch(
        "scripts.tennis.enrich_match_durations.get_match_duration", return_value=_DURATION,
    ):
        result = enrich_match_durations()

    assert result == {"processed": 1, "resolved": 1, "not_found": 0, "errors": 0}
    row = MatchHistory.get(MatchHistory.id == match.id)
    assert row.duration.total_minutes == 100


def test_reuses_flashscore_id_from_existing_stats():
    match = _make_match()
    MatchStats.create(
        match_history=match, source="flashscore",
        flashscore_match_id="shared-id", raw_stats_json="{}",
        fetched_at=datetime.now(timezone.utc), is_usable=True,
    )
    with patch("scripts.tennis.enrich_match_durations.find_match_id") as find, patch(
        "scripts.tennis.enrich_match_durations.get_match_duration", return_value=_DURATION,
    ) as fetch:
        result = enrich_match_durations()

    find.assert_not_called()
    fetch.assert_called_once_with("shared-id")
    assert result["resolved"] == 1


def test_skips_matches_that_already_have_duration():
    match = _make_match()
    with patch(
        "scripts.tennis.enrich_match_durations.find_match_id", return_value="fs123",
    ), patch(
        "scripts.tennis.enrich_match_durations.get_match_duration", return_value=_DURATION,
    ):
        enrich_match_durations()  # 1ra corrida, resuelve
        result = enrich_match_durations()  # 2da corrida, nada que hacer

    assert result == {"processed": 0, "resolved": 0, "not_found": 0, "errors": 0}


def test_skips_matches_without_both_players_resolved():
    a = Player.create(canonical_name="Solo", created_at=datetime.now(timezone.utc))
    MatchHistory.create(
        match_id="test:noplayer", source="tennisexplorer", match_date="2025-01-01",
        player_a=a, player_b=None, fetched_at=datetime.now(timezone.utc),
    )
    with patch("scripts.tennis.enrich_match_durations.find_match_id") as mock_find:
        result = enrich_match_durations()

    assert result["processed"] == 0
    mock_find.assert_not_called()


def test_match_not_found_on_flashscore_counted_separately():
    _make_match()
    with patch(
        "scripts.tennis.enrich_match_durations.find_match_id", return_value=None,
    ):
        result = enrich_match_durations()

    assert result == {"processed": 1, "resolved": 0, "not_found": 1, "errors": 0}


def test_duration_fetch_failure_counted_as_not_found():
    _make_match()
    with patch(
        "scripts.tennis.enrich_match_durations.find_match_id", return_value="fs123",
    ), patch(
        "scripts.tennis.enrich_match_durations.get_match_duration", return_value=None,
    ):
        result = enrich_match_durations()

    assert result == {"processed": 1, "resolved": 0, "not_found": 1, "errors": 0}


def test_one_match_error_does_not_block_the_rest():
    _make_match()
    _make_match()
    with patch(
        "scripts.tennis.enrich_match_durations.find_match_id",
        side_effect=[Exception("boom"), "fs123"],
    ), patch(
        "scripts.tennis.enrich_match_durations.get_match_duration", return_value=_DURATION,
    ):
        result = enrich_match_durations()

    assert result["processed"] == 2
    assert result["errors"] == 1
    assert result["resolved"] == 1


def test_unresolvable_first_batch_does_not_block_later_matches():
    _make_match()
    _make_match()
    _make_match()

    with patch(
        "scripts.tennis.enrich_match_durations.find_match_id",
        side_effect=[None, None, "fs123"],
    ), patch(
        "scripts.tennis.enrich_match_durations.get_match_duration",
        return_value=_DURATION,
    ):
        first = enrich_match_durations(limit=2)
        # Simula reinicio del proceso: el cursor persistente en PostgreSQL/
        # SQLite debe conservar el avance aunque el global vuelva a cero.
        durations_module._batch_cursor_id = 0
        second = enrich_match_durations(limit=2)

    assert first["not_found"] == 2
    assert second["resolved"] == 1


def test_uses_tournament_name_and_score_from_match_history():
    from database.historical.tennis import Tournament

    t = Tournament.create(year=2025, tournament_name="Roland Garros", city="", fetched_at=datetime.now(timezone.utc))
    _make_match(tournament=t, score="7-6, 6-4")

    with patch(
        "scripts.tennis.enrich_match_durations.find_match_id", return_value="fs123",
    ) as mock_find, patch(
        "scripts.tennis.enrich_match_durations.get_match_duration", return_value=_DURATION,
    ):
        enrich_match_durations()

    _, kwargs = mock_find.call_args
    args = mock_find.call_args[0]
    assert args[3] == "Roland Garros"
    assert kwargs.get("expected_score") == "7-6, 6-4"
