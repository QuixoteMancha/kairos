"""Tests para src/web/cross_book — scan helpers."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.orchestration import cross_book
from src.core.orchestration.cross_book import _CROSS_BOOK_PLATFORMS, _norm_cb_name


# ── _norm_cb_name ─────────────────────────────────────────────────────────────


def test_norm_full_name():
    assert _norm_cb_name("Carlos Alcaraz") == "alcaraz_c"


def test_norm_with_initial():
    assert _norm_cb_name("C. Alcaraz") == "alcaraz_c"


def test_norm_lastname_only():
    assert _norm_cb_name("Alcaraz") == "alcaraz_"


def test_norm_strips_accents():
    assert _norm_cb_name("Cárlös Álcáràz") == "alcaraz_c"


def test_norm_multiple_first_names():
    # 'Mirra Andreeva' → 'andreeva_m'; 'Jean-Lennard Struff' → 'struff_j'
    assert _norm_cb_name("Mirra Andreeva") == "andreeva_m"
    assert _norm_cb_name("Jean Lennard Struff") == "struff_j"


def test_norm_empty():
    assert _norm_cb_name("") == ""
    assert _norm_cb_name("   ") == ""


def test_norm_cross_book_matching_via_frozenset():
    """Misma identidad de match cuando los names varían de formato."""
    a = frozenset([_norm_cb_name("Jannik Sinner"),  _norm_cb_name("Alexei Popyrin")])
    b = frozenset([_norm_cb_name("J. Sinner"),      _norm_cb_name("A. Popyrin")])
    c = frozenset([_norm_cb_name("Popyrin"),        _norm_cb_name("Sinner")])  # sin inicial
    assert a == b              # full name vs inicial → mismo set
    assert a != c              # 'sinner_' != 'sinner_j' → distinto


# ── _CROSS_BOOK_PLATFORMS ─────────────────────────────────────────────────────


def test_default_platforms_are_prediction_markets():
    # Sports bookies (stake/pinnacle/unibet/...) were archived during the
    # prediction-markets pivot (2026-07-03); Polymarket was removed entirely
    # 2026-07-09 (unfillable limit orders, apparent bot-driven queue-jumping
    # on their book) — the pool is now Kalshi only.
    assert set(_CROSS_BOOK_PLATFORMS) == {"kalshi"}


# NOTE: `_fetch_book_fixtures` (and its tests here) was removed 2026-07-10 along
# with the void-cancelled-fixture legacy concept (see settlement.py) — it was a
# no-op whose only callers were the removed void/live-hedge STAT paths.
# `_fetch_sport_raw_fixtures` (and its `_record_book_fetch` metrics helper)
# was also removed 2026-07-10 for the same reason (confirmed no-op, only
# caller `run_cross_book_scan` invoked it every cycle for nothing); no test
# coverage existed for it.
