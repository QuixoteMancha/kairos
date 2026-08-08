"""Tests de la validación de candidatos deduplicados de
`TennisMatchHistory` contra `TmlDatabaseMatch` (Sackmann, sin red) --
2026-08-08, pedido explícito del usuario: "Validaremos los matches de
tennisexplorer con la data de sackmann histórica para validar la
deduplicación, y los que no se puedan validar los dejamos en una tabla
aparte"."""

from __future__ import annotations

from scripts.tennis.validate_match_history_against_sackmann import (
    _parse_sets, scores_match, build_sackmann_indexes, validate_paired, validate_singleton,
)


def test_parse_sets_comma_separated():
    assert _parse_sets("6-4, 6-2") == [(6, 4), (6, 2)]


def test_parse_sets_space_separated():
    assert _parse_sets("6-4 6-2") == [(6, 4), (6, 2)]


def test_parse_sets_ignores_tiebreak_suffix():
    assert _parse_sets("7-6(4) 6-2") == [(7, 6), (6, 2)]


def test_parse_sets_ignores_retirement_suffix():
    assert _parse_sets("6-2 3-0 RET") == [(6, 2), (3, 0)]


def test_parse_sets_fail_closed_on_empty():
    assert _parse_sets(None) == []
    assert _parse_sets("") == []


def test_scores_match_when_a_is_winner_same_orientation():
    assert scores_match("6-4 6-2", "6-4, 6-2", a_is_winner=True) is True


def test_scores_match_reorients_when_a_is_loser():
    # Sackmann siempre winner-first; si player_a perdió, su score viene
    # loser-first -- hay que voltear cada set antes de comparar.
    assert scores_match("6-4 6-2", "4-6, 2-6", a_is_winner=False) is True


def test_scores_match_false_on_real_mismatch():
    assert scores_match("6-4 6-2", "6-3, 6-2", a_is_winner=True) is False


def test_scores_match_fail_closed_without_result():
    assert scores_match("6-4 6-2", "6-4, 6-2", a_is_winner=None) is False


def _tml_row(winner, loser, year=2024, score="6-4 6-2", w_rank=10, l_rank=20):
    return {
        "year": year, "winner_name": winner, "loser_name": loser,
        "winner_rank": w_rank, "winner_age": 25.0, "loser_rank": l_rank, "loser_age": 27.0,
        "score": score, "tourney_name": "Toronto",
    }


def test_build_indexes_by_pair_and_by_name():
    rows = [_tml_row("Aryna Sabalenka", "Amanda Anisimova")]
    by_pair, by_name = build_sackmann_indexes(rows)
    assert (2024, frozenset({"aryna sabalenka", "amanda anisimova"})) in by_pair
    assert (2024, "aryna sabalenka") in by_name
    assert (2024, "amanda anisimova") in by_name


def test_validate_paired_confirms_unique_match():
    paired = [{
        "match_date": "2024-08-10", "tournament": "Toronto WTA",
        "player_a": "Amanda Anisimova", "player_b": "Aryna Sabalenka",
        "round": "QF", "surface": "hard", "score_from_a": "6-4, 6-2", "result_a": "W",
    }]
    by_pair, _ = build_sackmann_indexes([_tml_row("Amanda Anisimova", "Aryna Sabalenka")])
    validated, unvalidated = validate_paired(paired, by_pair)
    assert len(validated) == 1
    assert unvalidated == []
    assert validated[0]["a_is_winner"] is True
    assert validated[0]["player_a_ranking"] == 10
    assert validated[0]["player_b_ranking"] == 20


def test_validate_paired_unvalidated_when_no_sackmann_row():
    paired = [{
        "match_date": "2024-08-10", "tournament": "Some ITF $10K",
        "player_a": "Nobody Known", "player_b": "Also Nobody",
        "round": "1R", "surface": "hard", "score_from_a": "6-4, 6-2", "result_a": "W",
    }]
    validated, unvalidated = validate_paired(paired, {})
    assert validated == []
    assert unvalidated[0]["reason"] == "no_sackmann_match"


def test_validate_paired_disambiguates_by_score_when_multiple_meetings():
    paired = [{
        "match_date": "2024-08-10", "tournament": "Toronto WTA",
        "player_a": "Amanda Anisimova", "player_b": "Aryna Sabalenka",
        "round": "QF", "surface": "hard", "score_from_a": "6-4, 6-2", "result_a": "W",
    }]
    by_pair, _ = build_sackmann_indexes([
        _tml_row("Amanda Anisimova", "Aryna Sabalenka", score="6-4 6-2"),  # esta es la real
        _tml_row("Amanda Anisimova", "Aryna Sabalenka", score="7-5 6-3"),  # otro encuentro el mismo año
    ])
    validated, unvalidated = validate_paired(paired, by_pair)
    assert len(validated) == 1
    assert validated[0]["sackmann_row"]["score"] == "6-4 6-2"


def test_validate_paired_unvalidated_when_score_cannot_disambiguate():
    paired = [{
        "match_date": "2024-08-10", "tournament": "Toronto WTA",
        "player_a": "Amanda Anisimova", "player_b": "Aryna Sabalenka",
        "round": "QF", "surface": "hard", "score_from_a": "6-1, 6-1",  # no calza con ninguno
        "result_a": "W",
    }]
    by_pair, _ = build_sackmann_indexes([
        _tml_row("Amanda Anisimova", "Aryna Sabalenka", score="6-4 6-2"),
        _tml_row("Amanda Anisimova", "Aryna Sabalenka", score="7-5 6-3"),
    ])
    validated, unvalidated = validate_paired(paired, by_pair)
    assert validated == []
    assert unvalidated[0]["reason"] == "ambiguous_sackmann_match"


def test_validate_singleton_resolves_full_name_via_abbreviation():
    singleton = [{
        "match_date": "2024-08-10", "tournament": "Toronto WTA",
        "player_a": "Amanda Anisimova", "player_b_abbrev": "Sabalenka A.",
        "round": "QF", "surface": "hard", "score_from_a": "6-4, 6-2", "result_a": "W",
    }]
    _, by_name = build_sackmann_indexes([_tml_row("Amanda Anisimova", "Aryna Sabalenka")])
    validated, unvalidated = validate_singleton(singleton, by_name)
    assert len(validated) == 1
    assert validated[0]["player_b"] == "Aryna Sabalenka"


def test_validate_singleton_unvalidated_when_abbreviation_does_not_resolve():
    singleton = [{
        "match_date": "2024-08-10", "tournament": "Toronto WTA",
        "player_a": "Amanda Anisimova", "player_b_abbrev": "Nadie X.",
        "round": "QF", "surface": "hard", "score_from_a": "6-4, 6-2", "result_a": "W",
    }]
    _, by_name = build_sackmann_indexes([_tml_row("Amanda Anisimova", "Aryna Sabalenka")])
    validated, unvalidated = validate_singleton(singleton, by_name)
    assert validated == []
    assert unvalidated[0]["reason"] == "no_sackmann_match"
