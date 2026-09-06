"""Tests de `age_fatigue_veto` -- 2026-09-05, pedido explícito del
usuario tras el análisis histórico de edad x fatiga x resultado."""

from __future__ import annotations

import pytest

from outer_apis.sports.tennis.age_fatigue_veto import age_fatigue_veto


@pytest.mark.parametrize("age,cum_minutes,expected", [
    (31.5, 529, True),   # 30+ y muy fatigado -> veta
    (30.0, 270, True),   # exactamente en el umbral de ambos -> veta
    (32.0, 130, False),  # 30+ pero fatiga baja -> no veta
    (24.3, 503, False),  # muy fatigado pero menor de 30 -> no veta
    (29.9, 300, False),  # justo por debajo del umbral de edad -> no veta
    (35.0, 269.9, False),  # justo por debajo del umbral de fatiga -> no veta
])
def test_age_fatigue_veto(age, cum_minutes, expected):
    assert age_fatigue_veto(age, cum_minutes) is expected


def test_fails_closed_on_missing_age():
    assert age_fatigue_veto(None, 500) is False


def test_fails_closed_on_missing_fatigue():
    assert age_fatigue_veto(35.0, None) is False


def test_fails_closed_on_both_missing():
    assert age_fatigue_veto(None, None) is False
