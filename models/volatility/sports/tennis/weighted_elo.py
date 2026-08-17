"""Weighted Elo tenis: recencia, margen de sets y mezcla por superficie."""

from __future__ import annotations

from datetime import date
from math import exp, log

BASE_ELO = 1500.0
K_FACTOR = 32.0
INACTIVITY_HALF_LIFE_DAYS = 365.0
SURFACE_WEIGHT = 0.5


def regress_for_inactivity(rating: float, days: int) -> float:
    if days <= 0:
        return rating
    retained = 0.5 ** (days / INACTIVITY_HALF_LIFE_DAYS)
    return BASE_ELO + (rating - BASE_ELO) * retained


def expected_score(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))


def combined_probability(
    overall_a: float, overall_b: float, surface_a: float, surface_b: float,
) -> float:
    return (
        (1.0 - SURFACE_WEIGHT) * expected_score(overall_a, overall_b)
        + SURFACE_WEIGHT * expected_score(surface_a, surface_b)
    )


def margin_multiplier(set_margin: int) -> float:
    return min(1.5, 1.0 + 0.15 * max(0, set_margin - 1))


def update_pair(a: float, b: float, a_won: bool, set_margin: int = 1) -> tuple[float, float]:
    delta = K_FACTOR * margin_multiplier(set_margin) * ((1.0 if a_won else 0.0) - expected_score(a, b))
    return a + delta, b - delta


def fit_platt(observations: list[tuple[float, bool]]) -> tuple[float, float]:
    """Ajusta sigmoid(intercept + slope*logit(p)); identidad con N pequeño."""
    if len(observations) < 100:
        return 0.0, 1.0
    intercept, slope = 0.0, 1.0
    for _ in range(1200):
        grad_i = grad_s = 0.0
        for probability, won in observations:
            p = min(1 - 1e-6, max(1e-6, probability))
            x = log(p / (1 - p))
            fitted = 1.0 / (1.0 + exp(-(intercept + slope * x)))
            error = fitted - float(won)
            grad_i += error
            grad_s += error * x
        n = len(observations)
        intercept -= 0.05 * grad_i / n
        slope -= 0.05 * (grad_s / n + 0.001 * (slope - 1.0))
    return intercept, slope


def calibrate(probability: float, intercept: float, slope: float) -> float:
    p = min(1 - 1e-6, max(1e-6, probability))
    x = log(p / (1 - p))
    return 1.0 / (1.0 + exp(-(intercept + slope * x)))
