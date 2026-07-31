"""Tests de 'winrate vs grupo de rivales similares' (2026-07-31, pedido
explícito del usuario: para cuando no hay H2H directo entre 2 jugadores).
Mockea toda lectura de DB/fetch externo -- construye los pools de
candidatos a mano."""

from __future__ import annotations

from unittest.mock import patch

from outer_apis.sports.tennis.style_peer_group import (
    get_style_peer_group, get_winrate_vs_peer_group, _minmax_normalize, _style_vector,
)


def _split(win_pct, hold_pct, break_pct, ace_pct, df_pct):
    return {"win_pct": win_pct, "hold_pct": hold_pct, "break_pct": break_pct,
            "ace_pct": ace_pct, "df_pct": df_pct}


def test_style_vector_none_when_field_missing():
    assert _style_vector({"ace_pct": 0.1, "df_pct": None, "hold_pct": 0.8, "break_pct": 0.3}) is None
    assert _style_vector({"ace_pct": 0.1, "df_pct": 0.02, "hold_pct": 0.8, "break_pct": 0.3}) == (0.1, 0.02, 0.8, 0.3)


def test_minmax_normalize_handles_constant_dimension():
    vectors = [(1.0, 5.0), (2.0, 5.0), (3.0, 5.0)]
    normalized = _minmax_normalize(vectors)
    assert normalized[0][1] == 0.0 and normalized[1][1] == 0.0  # dimensión constante -> 0.0, sin dividir por cero
    assert normalized[0][0] == 0.0 and normalized[2][0] == 1.0


def test_get_style_peer_group_none_when_opponent_rank_missing():
    assert get_style_peer_group("Rival X", None, "Hard", "ATP") is None


def test_get_style_peer_group_none_when_opponent_has_no_split():
    with patch("outer_apis.sports.tennis.style_peer_group.get_career_win_pct_by_surface", return_value=None):
        assert get_style_peer_group("Rival X", 50, "Hard", "ATP") is None


def test_get_style_peer_group_filters_by_rank_window_and_ranks_by_style_distance():
    opponent_split = _split(0.7, 0.8, 0.25, 0.09, 0.03)  # rival: ace%=0.09
    all_splits = {
        "Close Style": _split(0.6, 0.79, 0.24, 0.095, 0.028),   # estilo MUY parecido, rank dentro de la ventana
        "Far Style": _split(0.5, 0.5, 0.5, 0.20, 0.10),          # estilo muy distinto, rank dentro de la ventana
        "Out Of Rank Window": _split(0.6, 0.80, 0.25, 0.09, 0.03),  # estilo idéntico pero rank MUY lejos
    }
    rankings = {"Close Style": 55, "Far Style": 45, "Out Of Rank Window": 500}

    with patch("outer_apis.sports.tennis.style_peer_group.get_career_win_pct_by_surface", return_value=opponent_split), \
         patch("outer_apis.sports.tennis.style_peer_group.get_all_career_splits_for_surface", return_value=all_splits), \
         patch("outer_apis.sports.tennis.style_peer_group.get_latest_rankings", return_value=rankings):
        peers = get_style_peer_group("Rival X", 50, "Hard", "ATP")

    # Solo 2 candidatos caen dentro de la ventana de ranking (±40 de 50) --
    # MIN_PEER_GROUP_SIZE=3 no se alcanza -> None (fail-closed).
    assert peers is None


def test_get_winrate_vs_peer_group_fail_closed_below_min_matches():
    with patch("outer_apis.sports.tennis.style_peer_group.get_style_peer_group", return_value=["P1", "P2", "P3"]):
        with patch("outer_apis.sports.tennis.style_peer_group.get_all_surface_matches", return_value=[
            {"opponent": "P1", "match_date": "2026-01-01", "result": "W"},
        ]):
            assert get_winrate_vs_peer_group("Player A", "Rival X", 50, "Hard", "ATP") is None


def test_get_winrate_vs_peer_group_computes_real_winrate():
    with patch("outer_apis.sports.tennis.style_peer_group.get_style_peer_group", return_value=["P1", "P2", "P3"]):
        matches = [
            {"opponent": "P1", "match_date": "2026-01-01", "result": "W"},
            {"opponent": "P2", "match_date": "2026-01-05", "result": "L"},
            {"opponent": "P3", "match_date": "2026-01-10", "result": "W"},
            {"opponent": "Someone Not In Peer Group", "match_date": "2026-01-15", "result": "L"},
        ]
        with patch("outer_apis.sports.tennis.style_peer_group.get_all_surface_matches", return_value=matches):
            result = get_winrate_vs_peer_group("Player A", "Rival X", 50, "Hard", "ATP")

    assert result["n_matches"] == 3  # el partido contra alguien fuera del grupo no cuenta
    assert result["win_rate"] == round(2 / 3, 4)
