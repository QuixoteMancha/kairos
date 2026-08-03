"""Tests de la integración set1_favorite_reaction + score en vivo
(2026-08-02, pedido explícito del usuario).

FUSIONADO 2026-08-02: este módulo ya no corre como worker propio (ver
docstring de `workers/set1_favorite_reaction_worker.py`) -- se testea
`check_and_save_set1_signal` como función pura, invocada por
`tennis_match_tick_worker.py` (ver `tests/test_tennis_match_tracking.py`
para el test de integración/aislamiento de fallas)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from database.db import init_db, db
from database.accounting.set1entrysignal import Set1EntrySignal
from workers.set1_favorite_reaction_worker import check_and_save_set1_signal


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
    with patch("workers.set1_favorite_reaction_worker.get_live_matches", return_value=[
             {"player_a": "Player A", "player_b": "Player B"},
         ]), \
         patch("workers.set1_favorite_reaction_worker.get_set1_winner", return_value="a"), \
         patch("workers.set1_favorite_reaction_worker._build_name_to_rank", return_value={}), \
         patch("workers.set1_favorite_reaction_worker._lookup_rank", side_effect=lambda name, c: 3 if name == "Player A" else 28):
        outcome = check_and_save_set1_signal(match, {}, {})

    assert outcome == "processed"
    signal = Set1EntrySignal.get(Set1EntrySignal.market_id == "KX1")
    assert signal.favorite_name == "Player A"
    assert signal.favorite_won_set1 is True
    assert signal.size_pct == 0.01
    assert signal.rank_gap == 25


def test_creates_signal_when_favorite_loses_set1():
    match = _open_match()
    with patch("workers.set1_favorite_reaction_worker.get_live_matches", return_value=[
             {"player_a": "Player A", "player_b": "Player B"},
         ]), \
         patch("workers.set1_favorite_reaction_worker.get_set1_winner", return_value="b"), \
         patch("workers.set1_favorite_reaction_worker._build_name_to_rank", return_value={}), \
         patch("workers.set1_favorite_reaction_worker._lookup_rank", side_effect=lambda name, c: 3 if name == "Player A" else 28):
        check_and_save_set1_signal(match, {}, {})

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
    with patch("workers.set1_favorite_reaction_worker.get_live_matches") as mock_live:
        outcome = check_and_save_set1_signal(match, {}, {})

    assert outcome == "skipped"
    mock_live.assert_not_called()  # ni siquiera pide el score, ya está registrado


def test_skips_when_match_not_found_in_live_source():
    match = _open_match()
    with patch("workers.set1_favorite_reaction_worker.get_live_matches", return_value=[]):
        outcome = check_and_save_set1_signal(match, {}, {})

    assert outcome == "skipped"
    assert Set1EntrySignal.select().count() == 0


def test_skips_when_set1_still_in_progress():
    match = _open_match()
    with patch("workers.set1_favorite_reaction_worker.get_live_matches", return_value=[
             {"player_a": "Player A", "player_b": "Player B"},
         ]), \
         patch("workers.set1_favorite_reaction_worker.get_set1_winner", return_value=None):
        outcome = check_and_save_set1_signal(match, {}, {})

    assert outcome == "skipped"
    assert Set1EntrySignal.select().count() == 0


def test_skips_fail_closed_when_no_real_rank():
    match = _open_match()
    with patch("workers.set1_favorite_reaction_worker.get_live_matches", return_value=[
             {"player_a": "Player A", "player_b": "Player B"},
         ]), \
         patch("workers.set1_favorite_reaction_worker.get_set1_winner", return_value="a"), \
         patch("workers.set1_favorite_reaction_worker._build_name_to_rank", return_value={}), \
         patch("workers.set1_favorite_reaction_worker._lookup_rank", return_value=None):
        outcome = check_and_save_set1_signal(match, {}, {})

    assert outcome == "skipped"
    assert Set1EntrySignal.select().count() == 0


def test_shares_caches_across_calls_for_same_tour():
    """rank_cache/live_cache pasados desde afuera deben reusarse -- no
    se debe volver a pedir get_live_matches/_build_name_to_rank para el
    mismo tour dentro del mismo ciclo (ver tennis_match_tick_worker.py)."""
    match1 = _open_match(market_id="KX1", player_a="Player A", player_b="Player B")
    match2 = _open_match(market_id="KX2", player_a="Player C", player_b="Player D")
    rank_cache: dict = {}
    live_cache: dict = {}

    with patch("workers.set1_favorite_reaction_worker.get_live_matches", return_value=[
             {"player_a": "Player A", "player_b": "Player B"},
             {"player_a": "Player C", "player_b": "Player D"},
         ]) as mock_live, \
         patch("workers.set1_favorite_reaction_worker.get_set1_winner", return_value="a"), \
         patch("workers.set1_favorite_reaction_worker._build_name_to_rank", return_value={}) as mock_rank_lookup, \
         patch("workers.set1_favorite_reaction_worker._lookup_rank", return_value=5):
        check_and_save_set1_signal(match1, rank_cache, live_cache)
        check_and_save_set1_signal(match2, rank_cache, live_cache)

    mock_live.assert_called_once()  # mismo tour ("WTA"), un solo fetch para los 2 partidos
    mock_rank_lookup.assert_called_once()
