"""
tests/test_tennis_total_games_bo5.py — real BO5 total-games model
(replaces the `total_games_bo3_from_serve(...) * 0.6` placeholder, 2026-07-17,
built at the user's explicit request after real manual-betting losses traced
to that placeholder's massive miscalibration).
"""
from __future__ import annotations

import pytest

from src.core.resolvers.sports.tennis import (
    _bo5_set_distributions,
    total_games_bo3_from_serve,
    total_games_bo5_from_serve,
    compute_tennis_total_games_prob,
)


def test_bo5_terminal_distribution_normalizes_to_one():
    """The 6 terminal nodes (3-0/3-1/3-2 either way) must cover 100% of the
    outcome space -- sum of all probabilities across all terminal dists is 1.0."""
    terminal = _bo5_set_distributions(0.65, 0.60)
    total_prob = sum(p for dist in terminal.values() for p in dist.values())
    assert total_prob == pytest.approx(1.0, abs=1e-6)
    # Exactly the 6 real terminal set-score states, nothing else.
    assert set(terminal.keys()) == {(3, 0), (0, 3), (3, 1), (1, 3), (3, 2), (2, 3)}


def test_bo5_minimum_possible_games_is_18():
    """A BO5 match cannot finish in fewer than 18 games (3 sets, 6-0 each --
    the fastest possible sweep). Any probability mass below 18 is a bug."""
    terminal = _bo5_set_distributions(0.65, 0.60)
    min_games_seen = min(t for dist in terminal.values() for t in dist.keys())
    assert min_games_seen >= 18


def test_bo5_real_model_differs_sharply_from_old_placeholder():
    """BUG REAL FIXED 2026-07-17: the old placeholder was
    `total_games_bo3_from_serve(...) * 0.6`. Confirm the real BO5 model
    produces a MUCH lower P(under) than that placeholder at a realistic
    threshold, matching the actual bug (placeholder massively overestimated
    P(under) for BO5 because it never accounted for the higher games floor)."""
    threshold = 22.5
    p_bo3 = total_games_bo3_from_serve(0.65, 0.60, threshold)
    p_bo5_real = total_games_bo5_from_serve(0.65, 0.60, threshold)
    p_old_placeholder = p_bo3 * 0.6

    assert p_bo5_real < p_old_placeholder / 5  # real model is dramatically lower
    assert 0.0 <= p_bo5_real <= 1.0


def test_bo5_p_under_increases_monotonically_with_threshold():
    p_low = total_games_bo5_from_serve(0.65, 0.60, 20.5)
    p_mid = total_games_bo5_from_serve(0.65, 0.60, 26.5)
    p_high = total_games_bo5_from_serve(0.65, 0.60, 34.5)
    assert p_low <= p_mid <= p_high


def test_compute_tennis_total_games_prob_routes_bo5_to_real_model():
    threshold = 24.5
    direct = total_games_bo5_from_serve(0.62, 0.58, threshold)
    via_compose = compute_tennis_total_games_prob(0.62, 0.58, bo_format="bo5", threshold=threshold)
    assert via_compose == pytest.approx(direct)


def test_compute_tennis_total_games_prob_bo3_still_real_model():
    threshold = 22.5
    direct = total_games_bo3_from_serve(0.62, 0.58, threshold)
    via_compose = compute_tennis_total_games_prob(0.62, 0.58, bo_format="bo3", threshold=threshold)
    assert via_compose == pytest.approx(direct)


def test_compute_tennis_total_games_prob_unknown_format_fails_closed():
    assert compute_tennis_total_games_prob(0.62, 0.58, bo_format="bo1", threshold=10.5) is None
