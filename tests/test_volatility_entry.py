"""Tests for src/core/strategies/volatility_entry.py — the unified
volatility+phase entry-level rule for Strategy 1 (Vesper's design, verified
by Sara). Pure math, no DB/network."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.core.strategies import volatility_entry as ve


def test_compute_delta_positive_and_bounded():
    delta = ve.compute_delta(0.55, m=0.5, sigma_fase=0.3)
    assert delta is not None
    assert 0.0 < delta < 0.55


def test_compute_delta_fails_closed_on_invalid_inputs():
    assert ve.compute_delta(0.0, 0.5, 0.3) is None
    assert ve.compute_delta(1.0, 0.5, 0.3) is None
    assert ve.compute_delta(0.5, -0.1, 0.3) is None
    assert ve.compute_delta(0.5, 0.5, -0.1) is None


def test_phase_multiplier_ordering_matches_qualitative_design():
    # mid > early ~= pre > tail (late), per Vesper's justification
    assert ve.PHASE_MULTIPLIERS["mid_match"] > ve.PHASE_MULTIPLIERS["early_match"]
    assert ve.PHASE_MULTIPLIERS["early_match"] > ve.PHASE_MULTIPLIERS["pre_match"]
    assert ve.PHASE_MULTIPLIERS["mid_match"] > ve.PHASE_MULTIPLIERS["late_match"]


def test_phase_multiplier_unknown_returns_none():
    assert ve.phase_multiplier("halftime_show") is None


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def test_estimate_phase_late_dominates_regardless_of_detection_time():
    now = datetime.now(timezone.utc)
    opened_at = _iso(now - timedelta(hours=10))  # detected long ago
    resolution = _iso(now + timedelta(hours=1))  # but resolves very soon
    phase = ve.estimate_event_phase(opened_at, resolution, now=now)
    assert phase == "late_match"


def test_estimate_phase_pre_early_mid_progression():
    now = datetime.now(timezone.utc)
    resolution = _iso(now + timedelta(hours=48))  # plenty of time left

    just_detected = _iso(now - timedelta(minutes=5))
    assert ve.estimate_event_phase(just_detected, resolution, now=now) == "pre_match"

    detected_a_bit_ago = _iso(now - timedelta(hours=1))
    assert ve.estimate_event_phase(detected_a_bit_ago, resolution, now=now) == "early_match"

    detected_long_ago = _iso(now - timedelta(hours=5))
    assert ve.estimate_event_phase(detected_long_ago, resolution, now=now) == "mid_match"


def test_estimate_phase_fails_closed_without_resolution_date():
    now = datetime.now(timezone.utc)
    assert ve.estimate_event_phase(_iso(now), None, now=now) is None
    assert ve.estimate_event_phase(_iso(now), "not-a-date", now=now) is None


def test_estimate_phase_fails_closed_without_opened_at_when_not_late():
    now = datetime.now(timezone.utc)
    resolution = _iso(now + timedelta(hours=48))
    assert ve.estimate_event_phase(None, resolution, now=now) is None


def test_propose_unified_levels_fails_closed_without_sigma_or_phase():
    assert ve.propose_unified_initial_levels(0.55, 0.45, sigma=None, phase="mid_match") is None
    assert ve.propose_unified_initial_levels(0.55, 0.45, sigma=0.3, phase=None) is None
    assert ve.propose_unified_initial_levels(0.55, 0.45, sigma=0.3, phase="unknown") is None
    assert ve.propose_unified_initial_levels(0.55, 0.45, sigma=0.0, phase="mid_match") is None


def test_propose_unified_levels_never_symmetric_and_meets_r_target():
    result = ve.propose_unified_initial_levels(
        p0_leader=0.70, p0_underdog_ask=0.30, sigma=0.8, phase="mid_match",
        r_target=0.02, margen_dog_floor=0.01,
    )
    assert result is not None
    # Never symmetric: underdog level must NOT equal leader's offset mirrored
    assert result.level_underdog != round(1.0 - result.level_leader, 10)
    total = result.level_leader + result.level_underdog
    assert total < 1.0
    roi = (1.0 - total) / total
    assert roi == pytest.approx(result.roi_if_both_fill, abs=1e-9)
    assert roi >= 0.02 - 1e-9


def test_propose_unified_levels_leader_price_always_below_market_price():
    # Safety invariant: level_leader < p0_leader ALWAYS, regardless of
    # sigma/phase quality -- single-leg fill is never worse than market.
    for sigma in (0.1, 0.5, 1.0, 2.0):
        for phase in ("pre_match", "early_match", "mid_match", "late_match"):
            result = ve.propose_unified_initial_levels(
                p0_leader=0.65, p0_underdog_ask=0.35, sigma=sigma, phase=phase,
                r_target=0.02, margen_dog_floor=0.01,
            )
            if result is not None:
                assert result.level_leader < 0.65


def test_propose_unified_levels_fails_closed_when_sigma_too_low_for_r_target():
    # Tiny sigma -> tiny delta even at m_max -> can't clear a demanding r_target.
    result = ve.propose_unified_initial_levels(
        p0_leader=0.55, p0_underdog_ask=0.45, sigma=0.001, phase="pre_match",
        r_target=0.10, margen_dog_floor=0.01,
    )
    assert result is None


def test_50_50_legacy_case_reproduces_small_epsilon_like_offset():
    # Near 50/50, early phase, moderate sigma -> delta should be small,
    # analogous to the legacy EPSILON_INITIAL=0.03 fixed offset (doc §4).
    result = ve.propose_unified_initial_levels(
        p0_leader=0.52, p0_underdog_ask=0.48, sigma=0.15, phase="early_match",
        r_target=0.01, margen_dog_floor=0.005,
    )
    assert result is not None
    assert result.delta < 0.10  # small offset, same ballpark as the legacy epsilon
    assert result.level_leader < 0.52
