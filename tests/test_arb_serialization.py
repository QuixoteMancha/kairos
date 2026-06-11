#!/usr/bin/env python3
"""
Tests de serialización de ArbOpportunity → dict.

Regresión: los detectores (scan_inside_arb, scan_stat_winner) devuelven objetos
ArbOpportunity que la capa web mezclaba con dicts y serializaba con jsonify() /
.setdefault() → AttributeError ('ArbOpportunity' object has no attribute
'setdefault'). Ahora ArbOpportunity.to_dict() los aplana a dicts JSON-safe.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.math.arbitrage.opportunity import MarketLine
from src.core.math.arbitrage.detectors.inside import scan_inside_arb


def _inside_arb():
    # overround 1/2.1 + 1/2.1 = 0.952 < 1 → arb garantizado
    ml = MarketLine(tipo="ganador", odd_p1=2.1, odd_p2=2.1)
    opps = scan_inside_arb([ml], 0.001, lambda x: "match")
    assert opps, "debería detectar 1 inside-arb"
    return opps[0]


def test_to_dict_is_json_serializable():
    d = _inside_arb().to_dict()
    # No debe lanzar: todos los valores son primitivos
    s = json.dumps(d)
    assert isinstance(s, str)


def test_to_dict_has_expected_keys():
    d = _inside_arb().to_dict()
    for k in ("tipo", "roi", "legs", "descripcion", "resolution_scope"):
        assert k in d
    assert isinstance(d["legs"], list) and len(d["legs"]) == 2


def test_to_dict_roi_is_percentage():
    d = _inside_arb().to_dict()
    # roi interno ~0.05 → expuesto como % ~5.0
    assert d["roi"] > 1.0


def test_to_dict_legs_are_flat_dicts():
    d = _inside_arb().to_dict()
    for leg in d["legs"]:
        assert isinstance(leg, dict)
        assert "side" in leg and "odd" in leg
        # market aplanado: no debe quedar el objeto MarketLine
        assert "market" not in leg


def test_setdefault_works_on_dict():
    # La capa web hace a.setdefault("sport", ...) — debe funcionar sobre el dict
    d = _inside_arb().to_dict()
    d.setdefault("sport", "tennis")
    assert d["sport"] == "tennis"
