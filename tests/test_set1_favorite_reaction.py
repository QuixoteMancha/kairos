"""Tests de la estrategia de sizing por reacción al set 1 (2026-08-02,
pedido explícito del usuario, caso real: Pegula vs Eala)."""

from __future__ import annotations

from strategies.set1_favorite_reaction import (
    recommend_set1_entry, get_favorite_win_rate_after_set1,
    SET1_ENTRY_SIZE_PCT, FAVORITE_WIN_RATE_AFTER_SET1_OVERALL,
    FAVORITE_WIN_RATE_AFTER_SET1_BY_RANK_GAP,
)


def test_no_entry_when_favorite_loses_set1():
    result = recommend_set1_entry(favorite_won_set1=False, rank_gap=25)
    assert result["size_pct"] == 0.0
    assert result["side"] is None


def test_entry_at_fixed_1pct_when_favorite_wins_set1():
    result = recommend_set1_entry(favorite_won_set1=True, rank_gap=25)
    assert result["size_pct"] == SET1_ENTRY_SIZE_PCT == 0.01
    assert result["side"] == "favorite"


def test_win_rate_basis_uses_correct_rank_gap_bucket():
    result = recommend_set1_entry(favorite_won_set1=True, rank_gap=25)
    assert result["favorite_win_rate_basis"] == FAVORITE_WIN_RATE_AFTER_SET1_BY_RANK_GAP["20-50"]


def test_win_rate_falls_back_to_overall_without_rank_gap():
    result = recommend_set1_entry(favorite_won_set1=True, rank_gap=None)
    assert result["favorite_win_rate_basis"] == FAVORITE_WIN_RATE_AFTER_SET1_OVERALL


def test_get_favorite_win_rate_after_set1_buckets():
    assert get_favorite_win_rate_after_set1(5) == FAVORITE_WIN_RATE_AFTER_SET1_BY_RANK_GAP["0-20"]
    assert get_favorite_win_rate_after_set1(19) == FAVORITE_WIN_RATE_AFTER_SET1_BY_RANK_GAP["0-20"]
    assert get_favorite_win_rate_after_set1(20) == FAVORITE_WIN_RATE_AFTER_SET1_BY_RANK_GAP["20-50"]
    assert get_favorite_win_rate_after_set1(49) == FAVORITE_WIN_RATE_AFTER_SET1_BY_RANK_GAP["20-50"]
    assert get_favorite_win_rate_after_set1(50) == FAVORITE_WIN_RATE_AFTER_SET1_BY_RANK_GAP["50-100"]
    assert get_favorite_win_rate_after_set1(99) == FAVORITE_WIN_RATE_AFTER_SET1_BY_RANK_GAP["50-100"]
    assert get_favorite_win_rate_after_set1(100) == FAVORITE_WIN_RATE_AFTER_SET1_BY_RANK_GAP["100+"]
    assert get_favorite_win_rate_after_set1(500) == FAVORITE_WIN_RATE_AFTER_SET1_BY_RANK_GAP["100+"]


def test_pegula_eala_real_case():
    """Caso real que motivó el módulo: Pegula (#3) vs Eala (#28),
    gap real=25 -- bucket 20-50."""
    result = recommend_set1_entry(favorite_won_set1=True, rank_gap=25)
    assert result["size_pct"] == 0.01
    assert result["favorite_win_rate_basis"] == FAVORITE_WIN_RATE_AFTER_SET1_BY_RANK_GAP["20-50"]
