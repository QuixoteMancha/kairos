"""Tests de `age_fatigue_veto` -- 2026-09-05, pedido explícito del
usuario tras el análisis histórico de edad x fatiga x resultado, luego
ampliado a "todas las combinaciones que dan WR menor a 60%"."""

from __future__ import annotations

import pytest

from outer_apis.sports.tennis.age_fatigue_veto import age_fatigue_veto


@pytest.mark.parametrize("age,cum_minutes,expected", [
    # <23 años: veta desde 90min (WR 59.8%/58.1%/55.7% < 60%)
    (20.0, 89.9, False),
    (20.0, 90.0, True),
    (22.9, 500, True),
    (20.0, 30, False),  # <90min, todavía en el bucket "fresco" 62.8%
    # 23-27 años: NUNCA veta (mínimo real 60.0%, no <60%)
    (25.0, 1000, False),
    (23.0, 270, False),
    (26.9, 10000, False),
    # 27-30 años: veta solo desde 270min (WR 56.4%)
    (28.0, 269.9, False),
    (28.0, 270.0, True),
    (29.9, 500, True),
    # 30+ años: veta solo desde 270min (WR 57.5%, caso original del usuario)
    (31.5, 529, True),
    (30.0, 270, True),
    (32.0, 130, False),  # 30+ pero fatiga baja -> no veta
    (50.0, 269, False),
])
def test_age_fatigue_veto(age, cum_minutes, expected):
    assert age_fatigue_veto(age, cum_minutes) is expected


def test_fails_closed_on_missing_age():
    assert age_fatigue_veto(None, 500) is False


def test_fails_closed_on_missing_fatigue():
    assert age_fatigue_veto(35.0, None) is False


def test_fails_closed_on_both_missing():
    assert age_fatigue_veto(None, None) is False
