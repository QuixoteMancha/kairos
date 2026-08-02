"""Tests del worker de integración set1_favorite_reaction + score en
vivo (2026-08-02, pedido explícito del usuario)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from database.db import init_db, db
from database.accounting.set1entrysignal import Set1EntrySignal
from workers.set1_favorite_reaction_worker import run_once


@pytest.fixture(autouse=True)
def clean_db():
    init_db()
    with db.connection_context():
        Set1EntrySignal.delete().execute()
    yield
    with db.connection_context():
        Set1EntrySignal.delete().execute()


def _open_match(market_id="KX1", player_a="Player A", player_b="Player B", tour="WTA"):
    return {
        "market_id": market_id, "ticker_a": f"{market_id}-A", "ticker_b": f"{market_id}-B",
        "tour": tour, "surface": "Hard", "player_a_name": player_a, "player_b_name": player_b,
        "player_a_rank": None, "player_b_rank": None, "best_of": 3, "close_time": None,
    }


def test_creates_signal_when_favorite_wins_set1():
    match = _open_match()
    with patch("workers.set1_favorite_reaction_worker.get_open_scanned_matches", return_value=[match]), \
         patch("workers.set1_favorite_reaction_worker.get_live_matches", return_value=[
             {"player_a": "Player A", "player_b": "Player B"},
         ]), \
         patch("workers.set1_favorite_reaction_worker.get_set1_winner", return_value="a"), \
         patch("workers.set1_favorite_reaction_worker._build_name_to_rank", return_value={}), \
         patch("workers.set1_favorite_reaction_worker._lookup_rank", side_effect=lambda name, c: 3 if name == "Player A" else 28):
        result = run_once()

    assert result["processed"] == 1
    signal = Set1EntrySignal.get(Set1EntrySignal.market_id == "KX1")
    assert signal.favorite_name == "Player A"
    assert signal.favorite_won_set1 is True
    assert signal.size_pct == 0.01
    assert signal.rank_gap == 25


def test_creates_signal_when_favorite_loses_set1():
    match = _open_match()
    with patch("workers.set1_favorite_reaction_worker.get_open_scanned_matches", return_value=[match]), \
         patch("workers.set1_favorite_reaction_worker.get_live_matches", return_value=[
             {"player_a": "Player A", "player_b": "Player B"},
         ]), \
         patch("workers.set1_favorite_reaction_worker.get_set1_winner", return_value="b"), \
         patch("workers.set1_favorite_reaction_worker._build_name_to_rank", return_value={}), \
         patch("workers.set1_favorite_reaction_worker._lookup_rank", side_effect=lambda name, c: 3 if name == "Player A" else 28):
        run_once()

    signal = Set1EntrySignal.get(Set1EntrySignal.market_id == "KX1")
    assert signal.favorite_won_set1 is False
    assert signal.size_pct == 0.0


def test_idempotent_skips_already_registered_market():
    match = _open_match()
    Set1EntrySignal.create(
        market_id="KX1", favorite_name="Player A", underdog_name="Player B",
        favorite_rank=3, underdog_rank=28, rank_gap=25,
        favorite_won_set1=True, size_pct=0.01,
    )
    with patch("workers.set1_favorite_reaction_worker.get_open_scanned_matches", return_value=[match]), \
         patch("workers.set1_favorite_reaction_worker.get_live_matches") as mock_live:
        result = run_once()

    assert result["skipped"] == 1
    mock_live.assert_not_called()  # ni siquiera pide el score, ya está registrado


def test_skips_when_match_not_found_in_live_source():
    match = _open_match()
    with patch("workers.set1_favorite_reaction_worker.get_open_scanned_matches", return_value=[match]), \
         patch("workers.set1_favorite_reaction_worker.get_live_matches", return_value=[]):
        result = run_once()

    assert result["skipped"] == 1
    assert Set1EntrySignal.select().count() == 0


def test_skips_when_set1_still_in_progress():
    match = _open_match()
    with patch("workers.set1_favorite_reaction_worker.get_open_scanned_matches", return_value=[match]), \
         patch("workers.set1_favorite_reaction_worker.get_live_matches", return_value=[
             {"player_a": "Player A", "player_b": "Player B"},
         ]), \
         patch("workers.set1_favorite_reaction_worker.get_set1_winner", return_value=None):
        result = run_once()

    assert result["skipped"] == 1
    assert Set1EntrySignal.select().count() == 0


def test_skips_fail_closed_when_no_real_rank():
    match = _open_match()
    with patch("workers.set1_favorite_reaction_worker.get_open_scanned_matches", return_value=[match]), \
         patch("workers.set1_favorite_reaction_worker.get_live_matches", return_value=[
             {"player_a": "Player A", "player_b": "Player B"},
         ]), \
         patch("workers.set1_favorite_reaction_worker.get_set1_winner", return_value="a"), \
         patch("workers.set1_favorite_reaction_worker._build_name_to_rank", return_value={}), \
         patch("workers.set1_favorite_reaction_worker._lookup_rank", return_value=None):
        result = run_once()

    assert result["skipped"] == 1
    assert Set1EntrySignal.select().count() == 0


def test_no_open_matches_returns_zero_summary():
    with patch("workers.set1_favorite_reaction_worker.get_open_scanned_matches", return_value=[]):
        result = run_once()
    assert result == {"open": 0, "processed": 0, "skipped": 0, "errors": 0}
