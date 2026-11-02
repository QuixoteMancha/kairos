"""Weighted Elo tenis: recencia, margen de sets y mezcla por superficie."""

from __future__ import annotations

from datetime import date
from math import exp, log

import numpy as np

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
    """Ajusta sigmoid(intercept + slope*logit(p)); identidad con N pequeño.

    Mismo descenso de gradiente de siempre (1200 pasos, lr 0.05, L2 hacia
    slope=1), vectorizado con numpy: con ~1M observaciones el bucle en
    Python puro costaba ~35 min de CPU por ciclo del worker de Elo. Los
    coeficientes coinciden con la versión escalar salvo el orden de suma
    en coma flotante."""
    n = len(observations)
    if n < 100:
        return 0.0, 1.0
    probabilities = np.fromiter((p for p, _ in observations), dtype=float, count=n)
    won = np.fromiter((w for _, w in observations), dtype=float, count=n)
    p = np.clip(probabilities, 1e-6, 1 - 1e-6)
    x = np.log(p / (1 - p))  # el logit no depende de los coeficientes: se calcula una vez
    intercept, slope = 0.0, 1.0
    for _ in range(1200):
        error = 1.0 / (1.0 + np.exp(-(intercept + slope * x))) - won
        grad_i = error.sum()
        grad_s = error @ x
        intercept -= 0.05 * grad_i / n
        slope -= 0.05 * (grad_s / n + 0.001 * (slope - 1.0))
    return float(intercept), float(slope)


def calibrate(probability: float, intercept: float, slope: float) -> float:
    p = min(1 - 1e-6, max(1e-6, probability))
    x = log(p / (1 - p))
    return 1.0 / (1.0 + exp(-(intercept + slope * x)))
