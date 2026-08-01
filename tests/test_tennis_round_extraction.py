"""Tests de extracción de ronda del torneo desde el título/rules_secondary
real de Kalshi (2026-08-01, pedido explícito del usuario para el backtest
de convergencia en semifinales/finales)."""

from __future__ import annotations

from outer_apis.kalshi.markets.tennis import _extract_round


def test_extract_round_from_real_title_format():
    raw = {"title": "Will Brandon Nakashima win the Nakashima vs Fritz: Semifinal match?"}
    assert _extract_round(raw) == "Semifinal"


def test_extract_round_final():
    raw = {"title": "Will Carlos Alcaraz win the Alcaraz vs Sinner: Final match?"}
    assert _extract_round(raw) == "Final"


def test_extract_round_of_n():
    raw = {"title": "Will Player A win the Player A vs Player B: Round of 32 match?"}
    assert _extract_round(raw) == "Round of 32"


def test_extract_round_falls_back_to_rules_secondary():
    raw = {
        "title": "unexpected format without colon",
        "rules_secondary": "The 2026 ATP Washington Semifinal after a ball has been played...",
    }
    assert _extract_round(raw) == "Semifinal"


def test_extract_round_fail_closed_when_nothing_matches():
    raw = {"title": "garbage", "rules_secondary": "also garbage"}
    assert _extract_round(raw) is None
