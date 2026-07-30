"""Tests del scraper de TennisExplorer (superficie real por partido) y del
facade de forma reciente por superficie. Mockea toda request HTTP real --
nunca pega a TennisExplorer real en tests."""

from __future__ import annotations

from unittest.mock import patch

from outer_apis.scrapers.tennis.tennisexplorer_scraper import (
    _extract_names, _parse_singles_table, _norm,
)
from outer_apis.sports.tennis.surface_form import get_recent_form_by_surface

# Fragmento real (simplificado) capturado en vivo de tennisexplorer.com,
# 2026-07-30 -- ver kairos-tennis-surface-form-2026-07-30 memory.
_REAL_FRAGMENT = """
<tr class="head flags"><td class="t-name" colspan="3">Wimbledon</td></tr>
<tr class="one">
<td class="first time">02.07.</td>
<td class="s-color"><span title="grass" style="background-color:#559B43">&nbsp;</span></td>
<td class="t-name"><a href="/player/samsonova/">Samsonova L.</a> - <a href="/player/shnaider-51347/" class="notU"><strong>Shnaider D.</strong></a></td>
<td class="round" title="2. round">2R</td>
<td class="tl"><a href="/match-detail/?id=1" title="x">6-4, 4-6, 6-2</a></td>
</tr>
<tr class="two">
<td class="first time">30.06.</td>
<td class="s-color"><span title="grass" style="background-color:#559B43">&nbsp;</span></td>
<td class="t-name"><a href="/player/shnaider-51347/" class="notU"><strong>Shnaider D.</strong></a> - <a href="/player/lys/">Lys E.</a></td>
<td class="round" title="1. round">1R</td>
<td class="tl"><a href="/match-detail/?id=2" title="x">7-5, 6-1</a></td>
</tr>
"""


def test_extract_names_ignores_literal_dash_separator():
    fragment = (
        '<a href="/player/samsonova/">Samsonova L.</a> - '
        '<a href="/player/shnaider-51347/" class="notU"><strong>Shnaider D.</strong></a>'
    )
    assert _extract_names(fragment) == ("Samsonova L.", "Shnaider D.")


def test_parse_singles_table_orients_result_to_subject_player():
    subject_tokens = set(_norm("Diana Shnaider").split())
    matches = _parse_singles_table(_REAL_FRAGMENT, 2026, "shnaider-51347", subject_tokens)

    assert len(matches) == 2
    # Fila 1: "Samsonova - Shnaider", 6-4 4-6 6-2 -> Samsonova gana 2 sets a 1 -> Shnaider perdió.
    m1 = next(m for m in matches if m["opponent"] == "Samsonova L.")
    assert m1["result"] == "L"
    assert m1["surface"] == "Grass"
    assert m1["match_date"] == "2026-07-02"

    # Fila 2: "Shnaider - Lys", 7-5 6-1 -> Shnaider gana ambos sets -> ganó.
    m2 = next(m for m in matches if m["opponent"] == "Lys E.")
    assert m2["result"] == "W"
    assert m2["match_date"] == "2026-06-30"


def test_parse_singles_table_skips_unrecognized_surface():
    fragment_bad_surface = _REAL_FRAGMENT.replace('title="grass"', 'title="mystery"')
    subject_tokens = set(_norm("Diana Shnaider").split())
    matches = _parse_singles_table(fragment_bad_surface, 2026, "shnaider-51347", subject_tokens)
    assert matches == []  # fail-closed: superficie no reconocida -> fila descartada


def test_get_recent_form_by_surface_none_when_surface_unknown():
    assert get_recent_form_by_surface("Diana Shnaider", surface=None) is None
    assert get_recent_form_by_surface("Diana Shnaider", surface="") is None


def test_get_recent_form_by_surface_fail_closed_below_min_sample():
    fake_matches = [
        {"opponent": "X", "match_date": "2026-07-01", "surface": "Hard", "result": "W", "score": "6-4 6-3"},
        {"opponent": "Y", "match_date": "2026-06-01", "surface": "Hard", "result": "L", "score": "4-6 3-6"},
    ]  # solo 2 partidos en Hard, MIN_MATCHES_ON_SURFACE=3
    with patch("outer_apis.sports.tennis.surface_form._fetch_and_cache", return_value=fake_matches):
        assert get_recent_form_by_surface("Jugadora Test", surface="Hard") is None


def test_get_recent_form_by_surface_computes_win_rate_with_enough_sample():
    fake_matches = [
        {"opponent": "X", "match_date": "2026-07-01", "surface": "Hard", "result": "W", "score": "6-4 6-3"},
        {"opponent": "Y", "match_date": "2026-06-01", "surface": "Hard", "result": "W", "score": "6-4 6-3"},
        {"opponent": "Z", "match_date": "2026-05-01", "surface": "Hard", "result": "L", "score": "4-6 3-6"},
        {"opponent": "W", "match_date": "2026-04-01", "surface": "Clay", "result": "W", "score": "6-4 6-3"},
    ]
    with patch("outer_apis.sports.tennis.surface_form._fetch_and_cache", return_value=fake_matches):
        result = get_recent_form_by_surface("Jugadora Test", surface="Hard")

    assert result == {"win_rate": round(2 / 3, 4), "n_matches": 3}  # solo las 3 de Hard cuentan
