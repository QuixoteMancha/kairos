#!/usr/bin/env python3
"""
Tests de las mejoras de estrategia (devig + supresión de value bets de ruido).

Verifica:
  - _devig_two_way: probabilidades fair sin margen del libro.
  - _team_elo_lookup: sin rating real → (None, None).
  - _generate_model_stats_for_raws: SIN modelo real, no emite winner stat bets
    (antes se fabricaba ELO con hash → edge de ruido -EV). Con consenso devig el
    edge es <= 0, por lo que no hay apuestas fabricadas.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.orchestration.scanning import (
    _devig_two_way,
    _team_elo_lookup,
    _generate_model_stats_for_raws,
)


# ── _devig_two_way ────────────────────────────────────────────────────────────

def test_devig_sums_to_one():
    p1, p2 = _devig_two_way(2.0, 2.0)
    assert pytest.approx(p1 + p2, abs=1e-9) == 1.0
    assert pytest.approx(p1, abs=1e-9) == 0.5


def test_devig_removes_margin():
    # Libro con vig: 1.90 / 1.90 → implícitas suman > 1
    p1, p2 = _devig_two_way(1.90, 1.90)
    assert pytest.approx(p1 + p2, abs=1e-9) == 1.0
    # cada fair_p < implícita con vig (1/1.90 = 0.526)
    assert p1 < 0.526


def test_devig_favorite_higher_prob():
    p1, p2 = _devig_two_way(1.50, 2.70)  # p1 favorito
    assert p1 > p2
    assert pytest.approx(p1 + p2, abs=1e-9) == 1.0


def test_devig_invalid_odds():
    assert _devig_two_way(1.0, 2.0) == (0.0, 0.0)
    assert _devig_two_way(0, 0) == (0.0, 0.0)


# ── _team_elo_lookup ──────────────────────────────────────────────────────────

def test_team_elo_lookup_none_without_real_source():
    # Sin fuente de ratings de equipos, debe devolver (None, None)
    assert _team_elo_lookup("Lakers", "Celtics", "basketball") == (None, None)


# ── No se emiten value bets de ruido (clave del fix) ──────────────────────────

def _raw(p1, p2, o1, o2):
    return {"p1": p1, "p2": p2, "odd_p1": o1, "odd_p2": o2, "book": "pinnacle"}


def test_no_fabricated_winner_stats_basketball():
    # Con consenso devig (sin rating real) el edge es <= 0 → 0 apuestas fabricadas.
    raws = [_raw("Lakers", "Celtics", 1.90, 1.90),
            _raw("Heat", "Bucks", 2.10, 1.75)]
    out = _generate_model_stats_for_raws("basketball", raws, min_edge=0.02)
    assert out == []


def test_no_fabricated_winner_stats_baseball():
    raws = [_raw("Yankees", "RedSox", 1.85, 1.95)]
    out = _generate_model_stats_for_raws("baseball", raws, min_edge=0.02)
    assert out == []


def test_deterministic_no_random_edges():
    # Antes, el hash del nombre fabricaba edges aleatorios. Ahora debe ser
    # determinista y vacío para los mismos inputs en deportes sin modelo real.
    raws = [_raw("Team A", "Team B", 1.95, 1.95)]
    a = _generate_model_stats_for_raws("ice_hockey", raws, min_edge=0.02)
    b = _generate_model_stats_for_raws("ice_hockey", raws, min_edge=0.02)
    assert a == b == []
