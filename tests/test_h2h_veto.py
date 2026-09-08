"""Tests de `h2h_veto` -- 2026-09-07, pedido explícito del usuario tras
validar contra 174,875 partidos reales que el H2H directo en contra del
favorito por ranking predice una caída de WR por debajo de 60%."""

from __future__ import annotations

from outer_apis.sports.tennis.h2h_veto import MIN_H2H, has_sufficient_h2h, h2h_veto


def test_vetoes_when_h2h_favors_underdog_strongly():
    assert h2h_veto(0.20) is True


def test_vetoes_when_h2h_is_close_but_against_favourite():
    assert h2h_veto(0.40) is True


def test_does_not_veto_when_h2h_favours_the_favourite():
    assert h2h_veto(0.75) is False


def test_does_not_veto_on_exact_50_50_h2h():
    assert h2h_veto(0.50) is False


def test_legacy_veto_does_not_decide_on_missing_h2h_history():
    assert h2h_veto(None) is False


def test_h2h_sample_requirement_is_explicit():
    assert has_sufficient_h2h(MIN_H2H - 1) is False
    assert has_sufficient_h2h(MIN_H2H) is True
