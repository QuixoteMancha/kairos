"""Tests del scraper de score en vivo de TennisExplorer (2026-08-02,
pedido explícito del usuario). Usa fragmentos de HTML REALES capturados
en vivo el 2026-08-02 (Fritz/Nakashima, Borges en vivo) -- no HTML
inventado."""

from __future__ import annotations

from unittest.mock import patch

from outer_apis.scrapers.tennis.tennisexplorer_livescore_scraper import (
    get_live_matches, get_set1_winner, _parse_score_cell, _parse_player_row,
)

# Fragmento real capturado 2026-08-02: Fritz vs Nakashima, partido
# TERMINADO (Fritz 2 sets, Nakashima 1).
_REAL_FINISHED_MATCH = '''
<tr id="r110" class="one fRow bott">
    <td class="first time" rowspan="2">00:40</td>
    <td class="t-name"><a href="/player/fritz-f1aa7/">Fritz T.</a> (3)</td>
    <td class="result">2</td>
    <td class="score">6</td>
    <td class="score">3</td>
    <td class="score">6</td>
    <td class="score">&nbsp;</td>
    <td class="score">&nbsp;</td>
</tr>
<tr id="r110b" class="one">
    <td class="t-name"><a href="/player/nakashima-68876/">Nakashima B.</a></td>
    <td class="result">1</td>
    <td class="score">3</td>
    <td class="score">6</td>
    <td class="score">3</td>
    <td class="score">&nbsp;</td>
    <td class="score">&nbsp;</td>
</tr>
'''

# Fragmento real capturado 2026-08-02: partido EN VIVO real (Borges
# abajo, perdió tiebreak del set1 6-7, en curso el set 2 a 5).
_REAL_LIVE_MATCH_WITH_TIEBREAK = '''
<tr id="r13" class="two bott">
    <td class="first time" rowspan="2">17:10</td>
    <td class="t-name"><a href="/player/someone/">Someone X.</a></td>
    <td class="result">0</td>
    <td class="score">7<sup>7</sup></td>
    <td class="score">6</td>
    <td class="score">&nbsp;</td>
    <td class="score">&nbsp;</td>
    <td class="score">&nbsp;</td>
</tr>
<tr id="r13b" class="two">
    <td class="t-name"><a href="/player/borges-8d823/">Borges N.</a></td>
    <td class="result">0</td>
    <td class="score">6<sup>7</sup></td>
    <td class="score">5</td>
    <td class="score">&nbsp;</td>
    <td class="score">&nbsp;</td>
    <td class="score">&nbsp;</td>
</tr>
'''

_SCHEDULED_NOT_STARTED = '''
<tr id="s10" class="one fRow bott">
    <td class="first time" rowspan="2">16:00</td>
    <td class="t-name"><a href="/player/olar/">Olar J.</a></td>
    <td class="result">&nbsp;</td>
    <td class="score">&nbsp;</td>
    <td class="score">&nbsp;</td>
    <td class="score">&nbsp;</td>
    <td class="score">&nbsp;</td>
    <td class="score">&nbsp;</td>
</tr>
<tr id="s10b" class="one">
    <td class="t-name"><a href="/player/sborowsky/">Sborowsky Y.</a></td>
    <td class="result">&nbsp;</td>
    <td class="score">&nbsp;</td>
    <td class="score">&nbsp;</td>
    <td class="score">&nbsp;</td>
    <td class="score">&nbsp;</td>
    <td class="score">&nbsp;</td>
</tr>
'''


def test_parse_score_cell_plain_number():
    assert _parse_score_cell("6") == 6


def test_parse_score_cell_with_tiebreak_superscript():
    assert _parse_score_cell("6<sup>7</sup>") == 6


def test_parse_score_cell_empty():
    assert _parse_score_cell("&nbsp;") is None


def test_get_live_matches_parses_real_finished_match():
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_livescore_scraper._fetch_matches_page",
        return_value=_REAL_FINISHED_MATCH,
    ):
        matches = get_live_matches("ATP")

    assert len(matches) == 1
    m = matches[0]
    assert m["player_a"] == "Fritz T."
    assert m["player_b"] == "Nakashima B."
    assert m["sets_won_a"] == 2
    assert m["sets_won_b"] == 1
    assert m["set_scores_a"] == [6, 3, 6, None, None]
    assert m["set_scores_b"] == [3, 6, 3, None, None]
    assert m["is_finished"] is True


def test_get_live_matches_parses_tiebreak_correctly():
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_livescore_scraper._fetch_matches_page",
        return_value=_REAL_LIVE_MATCH_WITH_TIEBREAK,
    ):
        matches = get_live_matches("ATP")

    m = matches[0]
    assert m["set_scores_a"][0] == 7  # ganó el tiebreak 7-6(7)
    assert m["set_scores_b"][0] == 6
    assert m["is_finished"] is False


def test_get_live_matches_skips_not_started_matches():
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_livescore_scraper._fetch_matches_page",
        return_value=_SCHEDULED_NOT_STARTED,
    ):
        matches = get_live_matches("ATP")
    assert matches == []


def test_get_live_matches_fail_closed_on_fetch_failure():
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_livescore_scraper._fetch_matches_page",
        return_value=None,
    ):
        assert get_live_matches("ATP") == []


def test_get_live_matches_invalid_tour():
    assert get_live_matches("NOTATOUR") == []


def test_get_set1_winner_real_finished_match():
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_livescore_scraper._fetch_matches_page",
        return_value=_REAL_FINISHED_MATCH,
    ):
        winner = get_set1_winner("Fritz T.", "Nakashima B.", "ATP")
    assert winner == "a"  # Fritz ganó el set1 6-3


def test_get_set1_winner_works_regardless_of_argument_order():
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_livescore_scraper._fetch_matches_page",
        return_value=_REAL_FINISHED_MATCH,
    ):
        winner = get_set1_winner("Nakashima B.", "Fritz T.", "ATP")
    assert winner == "b"  # Fritz (segundo arg) ganó el set1


def test_get_set1_winner_none_when_match_not_found():
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_livescore_scraper._fetch_matches_page",
        return_value=_REAL_FINISHED_MATCH,
    ):
        assert get_set1_winner("Nobody A", "Nobody B", "ATP") is None


def test_get_set1_winner_none_when_set1_still_in_progress():
    html = '''
<tr id="x1"><td class="t-name"><a href="/player/a/">A X.</a></td><td class="result">0</td>
    <td class="score">&nbsp;</td><td class="score">&nbsp;</td></tr>
<tr id="x1b"><td class="t-name"><a href="/player/b/">B Y.</a></td><td class="result">0</td>
    <td class="score">2</td><td class="score">&nbsp;</td></tr>
'''
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_livescore_scraper._fetch_matches_page",
        return_value=html,
    ):
        assert get_set1_winner("A X.", "B Y.", "ATP") is None
