"""Tests para platform_fees.py — normalización de nombres de jugador y registro
de plataformas (moved+merged 2026-07-10 from test_norm_cb_name.py and
test_cross_book_helpers.py, which duplicated most of this coverage, when
orchestration/cross_book.py was decommissioned)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from src.core.orchestration.platform_fees import _ACTIVE_REAL_MONEY_PLATFORMS, _norm_player_name


# ── _norm_player_name ──────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Carlos Alcaraz", "alcaraz_c"),
        ("C. Alcaraz", "alcaraz_c"),
        ("Alcaraz", "alcaraz_"),
        ("CARLOS ALCARAZ", "alcaraz_c"),
        ("  Carlos   Alcaraz  ", "alcaraz_c"),
        ("Stefanos Tsitsipas", "tsitsipas_s"),
        ("Novak Djokovic", "djokovic_n"),
        # Acentos → normalizados con NFKD ASCII
        ("Cristian Garín", "garin_c"),
        ("Jérémy Chardy", "chardy_j"),
        # Casos borde
        ("", ""),
        ("   ", ""),
    ],
)
def test_norm_player_name(raw, expected):
    assert _norm_player_name(raw) == expected


def test_norm_player_name_handles_multiple_dots():
    # "J. R. Lopez" → último token = "lopez", primer carácter del primero = "j"
    assert _norm_player_name("J. R. Lopez") == "lopez_j"


def test_norm_strips_accents():
    assert _norm_player_name("Cárlös Álcáràz") == "alcaraz_c"


def test_norm_multiple_first_names():
    # 'Mirra Andreeva' → 'andreeva_m'; 'Jean-Lennard Struff' → 'struff_j'
    assert _norm_player_name("Mirra Andreeva") == "andreeva_m"
    assert _norm_player_name("Jean Lennard Struff") == "struff_j"


def test_norm_matching_via_frozenset():
    """Misma identidad de match cuando los names varían de formato."""
    a = frozenset([_norm_player_name("Jannik Sinner"),  _norm_player_name("Alexei Popyrin")])
    b = frozenset([_norm_player_name("J. Sinner"),      _norm_player_name("A. Popyrin")])
    c = frozenset([_norm_player_name("Popyrin"),        _norm_player_name("Sinner")])  # sin inicial
    assert a == b              # full name vs inicial → mismo set
    assert a != c              # 'sinner_' != 'sinner_j' → distinto


# ── _ACTIVE_REAL_MONEY_PLATFORMS ────────────────────────────────────────────────

def test_default_platforms_are_prediction_markets():
    # Sports bookies (stake/pinnacle/unibet/...) were archived during the
    # prediction-markets pivot (2026-07-03); Polymarket was removed entirely
    # 2026-07-09 (unfillable limit orders, apparent bot-driven queue-jumping
    # on their book) — the pool is now Kalshi only.
    assert set(_ACTIVE_REAL_MONEY_PLATFORMS) == {"kalshi"}


# NOTE: `_fetch_book_fixtures` (and its tests here) was removed 2026-07-10 along
# with the void-cancelled-fixture legacy concept (see settlement.py) — it was a
# no-op whose only callers were the removed void/live-hedge STAT paths.
# `_fetch_sport_raw_fixtures` (and its `_record_book_fetch` metrics helper)
# was also removed 2026-07-10 for the same reason (confirmed no-op, only
# caller `run_cross_book_scan` invoked it every cycle for nothing); no test
# coverage existed for it.
