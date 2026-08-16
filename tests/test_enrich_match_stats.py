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
from database.historical.tennis import MatchHistory, Player
from scripts.tennis.enrich_match_stats import enrich_match_stats

_MOD = "scripts.tennis.enrich_match_stats"


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        MatchHistory.delete().execute()
        Player.delete().execute()
    yield
    with db_historical.connection_context():
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


def test_resolves_and_saves_stats_for_finished_match():
    _match()
    with patch(f"{_MOD}.find_match_id", return_value="fsid"), patch(
        f"{_MOD}.get_match_statistics", return_value=_STATS,
    ), patch(f"{_MOD}.save_match_stats_for_history", return_value=True) as save, patch(
        f"{_MOD}.time.sleep",
    ):
        result = enrich_match_stats(limit=10)

    assert result["resolved"] == 1
    save.assert_called_once()
    assert save.call_args[0][1] == "fsid"


def test_skips_matches_still_in_progress():
    """Hallazgo real del deploy: los partidos EN CURSO devuelven un feed
    incompleto ("Last 10 balls") y ensucian la tabla con columnas
    tipadas en NULL. Solo se procesan partidos con fecha anterior a hoy
    -- el sync worker filtraba solo por `score`, por eso los pescaba."""
    today = datetime.now(timezone.utc).date().isoformat()
    _match(match_date=today)
    with patch(f"{_MOD}.find_match_id") as find, patch(f"{_MOD}.time.sleep"):
        result = enrich_match_stats(limit=10)

    find.assert_not_called()
    assert result["processed"] == 0


def test_skips_matches_without_score():
    _match(score=None)
    with patch(f"{_MOD}.find_match_id") as find, patch(f"{_MOD}.time.sleep"):
        result = enrich_match_stats(limit=10)

    find.assert_not_called()
    assert result["processed"] == 0


def test_skips_matches_that_already_have_stats():
    _match()
    with patch(f"{_MOD}.has_match_stats_for_history", return_value=True), patch(
        f"{_MOD}.find_match_id",
    ) as find, patch(f"{_MOD}.time.sleep"):
        result = enrich_match_stats(limit=10)

    find.assert_not_called()
    assert result["processed"] == 0


def test_counts_not_found_when_flashscore_match_unresolved():
    _match()
    with patch(f"{_MOD}.find_match_id", return_value=None), patch(
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
    with patch(f"{_MOD}.find_match_id", return_value="fsid"), patch(
        f"{_MOD}.get_match_statistics", return_value=None,
    ), patch(f"{_MOD}.save_match_stats_for_history") as save, patch(f"{_MOD}.time.sleep"):
        result = enrich_match_stats(limit=10)

    save.assert_not_called()
    assert result["not_found"] == 1


def test_one_failing_match_does_not_stop_the_batch():
    for i in range(3):
        a, b = _players()
        _match(match_id=f"m{i}", players=(a, b))

    with patch(
        f"{_MOD}.find_match_id",
        side_effect=[RuntimeError("red caída"), "fs2", "fs3"],
    ), patch(f"{_MOD}.get_match_statistics", return_value=_STATS), patch(
        f"{_MOD}.save_match_stats_for_history", return_value=True,
    ), patch(f"{_MOD}.time.sleep"):
        result = enrich_match_stats(limit=10)

    assert result["processed"] == 3
    assert result["errors"] == 1
    assert result["resolved"] == 2


def test_respects_limit():
    for i in range(5):
        a, b = _players()
        _match(match_id=f"m{i}", players=(a, b))

    with patch(f"{_MOD}.find_match_id", return_value="fsid") as find, patch(
        f"{_MOD}.get_match_statistics", return_value=_STATS,
    ), patch(f"{_MOD}.save_match_stats_for_history", return_value=True), patch(
        f"{_MOD}.time.sleep",
    ):
        result = enrich_match_stats(limit=2)

    assert find.call_count == 2
    assert result["processed"] == 2
