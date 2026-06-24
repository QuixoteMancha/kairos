"""
test_multisport_autobet.py — _normalize_ms_arb: ROI display y auto_bet_eligible.

Bug fijado: la clave real que setea _execute_multi_sport_auto_bets es "roi"
(ya escalado a porcentaje, roi_raw*100); "roi_pct" nunca existió en ese dict,
así que el log siempre mostraba "ROI 0.00%". Además "auto_bet_eligible"
comparaba ese mismo "roi" (porcentaje) contra min_roi (fracción) sin
convertir, quedando vacuamente casi siempre True.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.orchestration.multisport_autobet import _normalize_ms_arb, _MS_THRESHOLDS


def _arb(roi_pct=2.5, sport="basketball"):
    return {
        "sport": sport,
        "fixture": "A vs B",
        "p1": "A", "p2": "B",
        "odd_p1": 1.95, "odd_p2": 1.95,
        "book_p1": "pinnacle", "book_p2": "unibet",
        "stake_split": {"on_p1": 0.5, "on_p2": 0.5},
        "roi": roi_pct,  # ya en porcentaje, como lo setea el ejecutor real
    }


def test_normalize_reads_roi_not_roi_pct():
    out = _normalize_ms_arb(_arb(roi_pct=2.5))
    assert out["roi"] == 2.5
    assert "ROI 2.50%" in out["descripcion"]


def test_auto_bet_eligible_compares_matching_scales():
    min_roi = _MS_THRESHOLDS["basketball"]["min_roi"]  # fracción, p.ej. 0.015
    # roi justo por encima del umbral (en porcentaje) → debe ser eligible
    above = _arb(roi_pct=round(min_roi * 100, 2) + 0.5, sport="basketball")
    out_above = _normalize_ms_arb(above)
    assert out_above["auto_bet_eligible"] is True

    # roi justo por debajo del umbral → NO debe ser eligible
    below = _arb(roi_pct=round(min_roi * 100, 2) - 0.5, sport="basketball")
    out_below = _normalize_ms_arb(below)
    assert out_below["auto_bet_eligible"] is False
