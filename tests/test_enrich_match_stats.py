"""Tests de `enrich_match_stats` -- 2026-08-16, pedido explícito del
usuario ("sí, agregalo") tras destrabar `get_match_statistics`.

Hace falta aparte del sync worker porque ese solo cubre a los jugadores
de partidos NUEVOS de Kalshi: los ~4.500 partidos históricos de
`MatchHistory` nunca recibían stats por ese camino."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis import (
    MatchDuration, MatchHistory, Player, TennisMatchStats,
)
from scripts.tennis.enrich_match_stats import enrich_match_stats
import scripts.tennis.enrich_match_stats as stats_module
from database.historical.workerstate import HistoricalEnrichmentAttempt

_MOD = "scripts.tennis.enrich_match_stats"


@pytest.fixture(autouse=True)
def clean_db():
    stats_module._batch_cursor_id = 0
    init_db_historical()
    with db_historical.connection_context():
        HistoricalEnrichmentAttempt.delete().where(
            HistoricalEnrichmentAttempt.job_name == "tennis_match_stats"
        ).execute()
        TennisMatchStats.delete().execute()
        MatchHistory.delete().execute()
        Player.delete().execute()
    yield
    stats_module._batch_cursor_id = 0
    with db_historical.connection_context():
        HistoricalEnrichmentAttempt.delete().where(
            HistoricalEnrichmentAttempt.job_name == "tennis_match_stats"
        ).execute()
        TennisMatchStats.delete().execute()
        MatchHistory.delete().execute()
        Player.delete().execute()


_seq = iter(range(1000))


def _players():
    """Nombres únicos por llamada -- `Player.canonical_name` tiene índice
    único real, reusar el mismo nombre rompía el test, no el código."""
    now = datetime.now(timezone.utc)
    n = next(_seq)
    return (
        Player.create(canonical_name=f"Player A{n}", created_at=now),
        Player.create(canonical_name=f"Player B{n}", created_at=now),
    )


def _past_date() -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=5)).isoformat()


def _match(**overrides) -> MatchHistory:
    a, b = overrides.pop("players", (None, None))
    if a is None:
        a, b = _players()
    base = {
        "match_id": "m1", "match_date": _past_date(), "score": "6-4, 6-2",
        "player_a": a, "player_b": b, "fetched_at": datetime.now(timezone.utc),
    }
    base.update(overrides)
    return MatchHistory.create(**base)


_STATS = {"match": {"Aces": ("3", "0")}}
_IDENTITY = {"match_id": "fsid", "player_a_is_p1": True}


def test_resolves_and_saves_stats_for_finished_match():
    _match()
    with patch(f"{_MOD}.find_match_identity", return_value=_IDENTITY), patch(
        f"{_MOD}.get_match_statistics", return_value=_STATS,
    ), patch(f"{_MOD}.save_match_stats_for_history_status", return_value="saved") as save, patch(
        f"{_MOD}.time.sleep",
    ):
        result = enrich_match_stats(limit=10)

    assert result["resolved"] == 1
    save.assert_called_once()
    assert save.call_args[0][1] == "fsid"


def test_reuses_flashscore_id_with_verified_orientation():
    duration = MatchDuration.create(
        flashscore_match_id="shared-id", total_minutes=90,
        fetched_at=datetime.now(timezone.utc),
    )
    match = _match(duration=duration)
    assert stats_module.remember_match_stats_source(
        match.id, "shared-id", True,
    )
    with patch(f"{_MOD}.find_match_identity") as find, patch(
        f"{_MOD}.get_match_statistics", return_value=_STATS,
    ) as fetch, patch(
        f"{_MOD}.save_match_stats_for_history_status", return_value="saved",
    ), patch(f"{_MOD}.time.sleep"):
        result = enrich_match_stats(limit=10)

    find.assert_not_called()
    fetch.assert_called_once_with("shared-id")
    assert result["resolved"] == 1


def test_skips_matches_still_in_progress():
    """Hallazgo real del deploy: los partidos EN CURSO devuelven un feed
    incompleto ("Last 10 balls") y ensucian la tabla con columnas
    tipadas en NULL. Solo se procesan partidos con fecha anterior a hoy
    -- el sync worker filtraba solo por `score`, por eso los pescaba."""
    today = datetime.now(timezone.utc).date().isoformat()
    _match(match_date=today)
    with patch(f"{_MOD}.find_match_identity") as find, patch(f"{_MOD}.time.sleep"):
        result = enrich_match_stats(limit=10)

    find.assert_not_called()
    assert result["processed"] == 0


def test_skips_matches_without_score():
    _match(score=None)
    with patch(f"{_MOD}.find_match_identity") as find, patch(f"{_MOD}.time.sleep"):
        result = enrich_match_stats(limit=10)

    find.assert_not_called()
    assert result["processed"] == 0


def test_skips_matches_that_already_have_stats():
    _match()
    with patch(f"{_MOD}.has_match_stats_for_history", return_value=True), patch(
        f"{_MOD}.find_match_identity",
    ) as find, patch(f"{_MOD}.time.sleep"):
        result = enrich_match_stats(limit=10)

    find.assert_not_called()
    assert result["processed"] == 0


def test_counts_not_found_when_flashscore_match_unresolved():
    _match()
    with patch(f"{_MOD}.find_match_identity", return_value=None), patch(
        f"{_MOD}.get_match_statistics",
    ) as stats, patch(f"{_MOD}.time.sleep"):
        result = enrich_match_stats(limit=10)

    stats.assert_not_called()
    assert result["not_found"] == 1
    assert result["resolved"] == 0


def test_counts_not_found_when_no_stats_available():
    """Común en challenger/ITF: el partido existe pero Flashscore no
    trackea estadísticas. Fail-closed, no se guarda nada."""
    _match()
    with patch(f"{_MOD}.find_match_identity", return_value=_IDENTITY), patch(
        f"{_MOD}.get_match_statistics", return_value=None,
    ), patch(f"{_MOD}.save_match_stats_for_history_status") as save, patch(f"{_MOD}.time.sleep"):
        result = enrich_match_stats(limit=10)

    save.assert_not_called()
    assert result["not_found"] == 1


def test_one_failing_match_does_not_stop_the_batch():
    for i in range(3):
        a, b = _players()
        _match(match_id=f"m{i}", players=(a, b))

    with patch(
        f"{_MOD}.find_match_identity",
        side_effect=[RuntimeError("red caída"), _IDENTITY, _IDENTITY],
    ), patch(f"{_MOD}.get_match_statistics", return_value=_STATS), patch(
        f"{_MOD}.save_match_stats_for_history_status", return_value="saved",
    ), patch(f"{_MOD}.time.sleep"):
        result = enrich_match_stats(limit=10)

    assert result["processed"] == 3
    assert result["errors"] == 1
    assert result["resolved"] == 2


def test_respects_limit():
    for i in range(5):
        a, b = _players()
        _match(match_id=f"m{i}", players=(a, b))

    with patch(f"{_MOD}.find_match_identity", return_value=_IDENTITY) as find, patch(
        f"{_MOD}.get_match_statistics", return_value=_STATS,
    ), patch(f"{_MOD}.save_match_stats_for_history_status", return_value="saved"), patch(
        f"{_MOD}.time.sleep",
    ):
        result = enrich_match_stats(limit=2)

    assert find.call_count == 2
    assert result["processed"] == 2


def test_unresolvable_first_batch_does_not_block_later_matches():
    for i in range(3):
        a, b = _players()
        _match(match_id=f"rotate{i}", players=(a, b))

    with patch(
        f"{_MOD}.find_match_identity", side_effect=[None, None, _IDENTITY],
    ), patch(
        f"{_MOD}.get_match_statistics", return_value=_STATS,
    ), patch(
        f"{_MOD}.save_match_stats_for_history_status", return_value="saved",
    ), patch(f"{_MOD}.time.sleep"):
        first = enrich_match_stats(limit=2)
        second = enrich_match_stats(limit=2)

    assert first["not_found"] == 2
    assert second["resolved"] == 1
