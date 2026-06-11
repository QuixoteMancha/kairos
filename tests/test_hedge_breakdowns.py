#!/usr/bin/env python3
"""
Tests de los breakdowns de hedge + hedge_pfail_with_elo reconstruidos.

Estas funciones (_build_surface_breakdown, _build_gap_breakdown,
_build_elo_breakdown, hedge_pfail_with_elo) se perdieron en el refactor;
analytics.py las importaba → /api/analytics/hedge-patterns daba 500.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.math.hedge import (
    _build_surface_breakdown,
    _build_gap_breakdown,
    _build_elo_breakdown,
    hedge_pfail_with_elo,
)


@dataclass
class _Rec:
    status: str
    pnl: float
    superficie: str | None = None
    gap: float | None = None
    elo_diff: float | None = None


_RECORDS = [
    _Rec("won", 1.5, "Hard", 5, 30),
    _Rec("lost", -2.0, "Hard", 7, 200),
    _Rec("won", 1.0, "Clay", 3, 100),
    _Rec("lost", -1.0, "Clay", 9, 400),
    _Rec("won", 0.5, None, None, None),
]


def test_surface_breakdown_groups():
    out = _build_surface_breakdown(_RECORDS)
    surfaces = {row["surface"] for row in out}
    assert "Hard" in surfaces and "Clay" in surfaces and "unknown" in surfaces
    hard = next(r for r in out if r["surface"] == "Hard")
    assert hard["n"] == 2 and hard["won"] == 1 and hard["lost"] == 1


def test_gap_breakdown_buckets():
    out = _build_gap_breakdown(_RECORDS)
    buckets = {row["gap_bucket"] for row in out}
    assert "5-6" in buckets and "7-8" in buckets and "0-4" in buckets


def test_elo_breakdown_buckets():
    out = _build_elo_breakdown(_RECORDS)
    buckets = {row["elo_bucket"] for row in out}
    assert any("even" in b for b in buckets)
    assert any("dominant" in b for b in buckets)


def test_breakdowns_handle_empty():
    assert _build_surface_breakdown([]) == []
    assert _build_gap_breakdown([]) == []
    assert _build_elo_breakdown([]) == []


def test_hedge_pfail_neutral_vs_elo():
    r = hedge_pfail_with_elo(12.5, 22.5, elo_p1=1700, elo_p2=1500, player_side_low="p1")
    assert 0.0 <= r["p_fail_elo"] <= 1.0
    assert 0.0 <= r["p_fail_neutral"] <= 1.0
    assert r["elo_diff"] == 200.0
    assert isinstance(r["is_riskier_than_neutral"], bool)


def test_hedge_pfail_no_elo_is_neutral():
    r = hedge_pfail_with_elo(12.5, 22.5, elo_p1=None, elo_p2=None)
    # sin ELO → p_set_low = 0.5 → p_fail_elo ≈ p_fail_neutral
    assert abs(r["p_fail_elo"] - r["p_fail_neutral"]) < 1e-6
    assert r["elo_diff"] == 0.0
