"""
test_scales.py — módulo único de normalización de escalas financieras.

Garantiza que la desambiguación fracción/porcentaje del ROI es consistente y
que el ROI realizado se calcula desde el dinero real (net_pnl / total_staked).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.math.scales import (
    normalize_roi_fraction,
    roi_to_pct,
    realized_roi_fraction,
    realized_roi_pct,
)


def test_normalize_fraction_passthrough():
    # ya es fracción (<1.0) → tal cual
    assert normalize_roi_fraction(0.03) == 0.03
    assert normalize_roi_fraction(0.0) == 0.0
    assert normalize_roi_fraction(-0.05) == -0.05


def test_normalize_percentage_divided():
    # venía en porcentaje (>=1.0) → /100
    assert normalize_roi_fraction(3.0) == 0.03
    assert normalize_roi_fraction(100.0) == 1.0
    assert round(normalize_roi_fraction(450.0), 4) == 4.5


def test_normalize_robust_to_garbage():
    assert normalize_roi_fraction(None) == 0.0
    assert normalize_roi_fraction("abc") == 0.0
    assert normalize_roi_fraction("") == 0.0


def test_roi_to_pct_consistent_regardless_of_input_scale():
    # mismo 3% expresado en ambas escalas → mismo porcentaje
    assert roi_to_pct(0.03) == 3.0
    assert roi_to_pct(3.0) == 3.0


def test_realized_roi_from_money():
    # net +0.40 sobre 10 apostado = 4%
    assert round(realized_roi_fraction(10.0, 0.40), 4) == 0.04
    assert realized_roi_pct(10.0, 0.40) == 4.0


def test_realized_roi_zero_stake_safe():
    assert realized_roi_fraction(0.0, 5.0) == 0.0
    assert realized_roi_pct(0.0, 5.0) == 0.0


def test_realized_roi_negative():
    # arb con bug perdió: net -3.80 sobre 9.10
    assert realized_roi_pct(9.10, -3.80) == round(-3.80 / 9.10 * 100, 2)
