#!/usr/bin/env python3
"""
Tests del reparto de stake para arbitraje bloqueado (_arb_stake_weights).

Valida la corrección del BUG: el path de tenis no traía stake_pct → reparto
IGUAL (50/50) → no era un arb bloqueado. El fix reparte INVERSO a las cuotas
para que el retorno sea el mismo gane quien gane.
"""
from __future__ import annotations

from src.core.orchestration.settlement import _arb_stake_weights


def _legs(*odds, stake_pcts=None):
    out = []
    for i, o in enumerate(odds):
        leg = {"odd": o, "side": f"p{i+1}"}
        if stake_pcts is not None:
            leg["stake_pct"] = stake_pcts[i]
        out.append(leg)
    return out


def test_weights_inverse_to_odds_locks_arb():
    """Reparto inverso a las cuotas → retorno igual gane quien gane."""
    legs = _legs(1.16, 11.00)   # sin stake_pct (caso del bug de tenis)
    w = _arb_stake_weights(legs)
    assert abs(sum(w) - 1.0) < 1e-9
    # el favorito (1.16) recibe casi todo el stake
    assert w[0] > 0.85 and w[1] < 0.15
    # retorno bloqueado: stake_i * odd_i constante
    total = 100.0
    ret_p1 = total * w[0] * 1.16
    ret_p2 = total * w[1] * 11.00
    assert abs(ret_p1 - ret_p2) < 0.5   # mismo retorno gane quien gane


def test_weights_not_5050_for_unbalanced_odds():
    """El bug: 50/50 para cuotas desbalanceadas. El fix NO debe dar 50/50."""
    legs = _legs(1.16, 11.00)
    w = _arb_stake_weights(legs)
    assert not (abs(w[0] - 0.5) < 0.05)  # NO es 50/50


def test_weights_balanced_odds_near_5050():
    """Cuotas equilibradas (2.0 / 2.0) → reparto ~50/50 (correcto)."""
    legs = _legs(2.0, 2.0)
    w = _arb_stake_weights(legs)
    assert abs(w[0] - 0.5) < 1e-6 and abs(w[1] - 0.5) < 1e-6


def test_weights_respects_valid_provided_fractions():
    """Si las legs ya traen stake_pct que suma ~1.0, se respetan."""
    legs = _legs(1.5, 3.0, stake_pcts=[0.66, 0.34])
    w = _arb_stake_weights(legs)
    assert w == [0.66, 0.34]


def test_weights_recomputes_when_provided_is_scale_100():
    """stake_pct en escala 0-100 (suma 100, no 1.0) → recalcula desde odds."""
    legs = _legs(1.16, 11.00, stake_pcts=[90.4, 9.5])  # suma ~100, no 1.0
    w = _arb_stake_weights(legs)
    assert abs(sum(w) - 1.0) < 1e-9
    assert w[0] > 0.85   # recalculado inverso a odds, no usó el 90.4 crudo


def test_weights_three_way_inverse():
    """3-way (1x2): reparto inverso a las tres cuotas."""
    legs = _legs(2.81, 8.76, 3.07)   # p1, draw, p2
    w = _arb_stake_weights(legs)
    assert abs(sum(w) - 1.0) < 1e-9
    total = 100.0
    rets = [total * w[i] * o for i, o in enumerate([2.81, 8.76, 3.07])]
    # los tres retornos casi iguales (arb bloqueado)
    assert max(rets) - min(rets) < 0.5


def test_weights_empty_and_zero_odds():
    assert _arb_stake_weights([]) == []
    # odds inválidas (<=1) → último recurso reparto igual
    w = _arb_stake_weights(_legs(1.0, 1.0))
    assert w == [0.5, 0.5]
