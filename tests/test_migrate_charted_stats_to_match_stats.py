"""Tests de la migración de `ChartedMatchStats` -> `MatchStats` (FK a
`MatchHistory`) -- 2026-08-08, pedido explícito del usuario."""

from __future__ import annotations

from datetime import date

from scripts.tennis.migrate_charted_stats_to_match_stats import (
    build_match_history_index, resolve_match_history_id,
)


def _mh_row(id_, a, b, match_date):
    return {"id": id_, "player_a_name": a, "player_b_name": b, "match_date": match_date}


def test_resolves_exact_date_match():
    index = build_match_history_index([_mh_row(1, "Amanda Anisimova", "Aryna Sabalenka", "2024-08-10")])
    mh_id, reason = resolve_match_history_id("Amanda Anisimova", "Aryna Sabalenka", date(2024, 8, 10), index)
    assert mh_id == 1
    assert reason == "resolved"


def test_resolves_within_date_tolerance():
    # MatchHistory con fecha aproximada de inicio de torneo (source=sackmann)
    index = build_match_history_index([_mh_row(1, "Amanda Anisimova", "Aryna Sabalenka", "2024-08-05")])
    mh_id, reason = resolve_match_history_id("Amanda Anisimova", "Aryna Sabalenka", date(2024, 8, 10), index)
    assert mh_id == 1
    assert reason == "resolved"


def test_no_candidate_when_pair_never_played_by_date():
    index = build_match_history_index([_mh_row(1, "Amanda Anisimova", "Aryna Sabalenka", "2020-01-01")])
    mh_id, reason = resolve_match_history_id("Amanda Anisimova", "Aryna Sabalenka", date(2024, 8, 10), index)
    assert mh_id is None
    assert reason == "no_candidate"


def test_no_candidate_when_pair_never_met():
    index = build_match_history_index([_mh_row(1, "Player X", "Player Y", "2024-08-10")])
    mh_id, reason = resolve_match_history_id("Amanda Anisimova", "Aryna Sabalenka", date(2024, 8, 10), index)
    assert mh_id is None
    assert reason == "no_candidate"


def test_picks_closest_date_when_multiple_candidates():
    index = build_match_history_index([
        _mh_row(1, "Amanda Anisimova", "Aryna Sabalenka", "2024-06-01"),
        _mh_row(2, "Amanda Anisimova", "Aryna Sabalenka", "2024-08-09"),  # más cerca
    ])
    mh_id, reason = resolve_match_history_id("Amanda Anisimova", "Aryna Sabalenka", date(2024, 8, 10), index)
    assert mh_id == 2
    assert reason == "resolved"


def test_ambiguous_when_tied_distance():
    index = build_match_history_index([
        _mh_row(1, "Amanda Anisimova", "Aryna Sabalenka", "2024-08-05"),
        _mh_row(2, "Amanda Anisimova", "Aryna Sabalenka", "2024-08-15"),  # ambos a 5 días exactos
    ])
    mh_id, reason = resolve_match_history_id("Amanda Anisimova", "Aryna Sabalenka", date(2024, 8, 10), index)
    assert mh_id is None
    assert reason == "ambiguous"


def test_name_order_does_not_matter():
    index = build_match_history_index([_mh_row(1, "Aryna Sabalenka", "Amanda Anisimova", "2024-08-10")])
    mh_id, reason = resolve_match_history_id("Amanda Anisimova", "Aryna Sabalenka", date(2024, 8, 10), index)
    assert mh_id == 1
