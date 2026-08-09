"""Tests de la migración de clima a `MatchWeather.match_history` --
2026-08-09, pedido explícito del usuario: "que cuando haga query de
algún match se pueda obtener el clima del match... match.weather.humidity"."""

from __future__ import annotations

from datetime import date

from scripts.tennis.migrate_weather_to_match_history import (
    build_match_history_index_by_name, resolve_match_history_id_by_one_name,
)


def _mh_row(id_, a, b, match_date):
    return {"id": id_, "player_a_name": a, "player_b_name": b, "match_date": match_date}


def test_resolves_by_single_known_name_and_date():
    index = build_match_history_index_by_name([_mh_row(1, "Amanda Anisimova", "Aryna Sabalenka", "2024-08-10")])
    mh_id, reason = resolve_match_history_id_by_one_name("Amanda Anisimova", date(2024, 8, 10), index)
    assert mh_id == 1
    assert reason == "resolved"


def test_resolves_even_when_only_the_other_side_matches():
    # el rival real de TennisMatchHistory viene abreviado -- solo
    # `player_name` (el sujeto) es cierto, pero puede resolver aunque
    # aparezca del lado A o B en MatchHistory.
    index = build_match_history_index_by_name([_mh_row(1, "Aryna Sabalenka", "Amanda Anisimova", "2024-08-10")])
    mh_id, reason = resolve_match_history_id_by_one_name("Amanda Anisimova", date(2024, 8, 10), index)
    assert mh_id == 1


def test_no_candidate_when_name_unknown():
    index = build_match_history_index_by_name([_mh_row(1, "Amanda Anisimova", "Aryna Sabalenka", "2024-08-10")])
    mh_id, reason = resolve_match_history_id_by_one_name("Nobody Known", date(2024, 8, 10), index)
    assert mh_id is None
    assert reason == "no_candidate"


def test_within_tolerance_matches_approximate_sackmann_date():
    index = build_match_history_index_by_name([_mh_row(1, "Amanda Anisimova", "Aryna Sabalenka", "2024-08-05")])
    mh_id, reason = resolve_match_history_id_by_one_name("Amanda Anisimova", date(2024, 8, 10), index)
    assert mh_id == 1


def test_ambiguous_when_two_equally_close_candidates():
    index = build_match_history_index_by_name([
        _mh_row(1, "Amanda Anisimova", "Player X", "2024-08-05"),
        _mh_row(2, "Amanda Anisimova", "Player Y", "2024-08-15"),
    ])
    mh_id, reason = resolve_match_history_id_by_one_name("Amanda Anisimova", date(2024, 8, 10), index)
    assert mh_id is None
    assert reason == "ambiguous"


def test_picks_closest_when_not_tied():
    index = build_match_history_index_by_name([
        _mh_row(1, "Amanda Anisimova", "Player X", "2024-06-01"),
        _mh_row(2, "Amanda Anisimova", "Player Y", "2024-08-09"),
    ])
    mh_id, reason = resolve_match_history_id_by_one_name("Amanda Anisimova", date(2024, 8, 10), index)
    assert mh_id == 2
    assert reason == "resolved"
