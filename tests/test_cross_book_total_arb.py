"""
test_cross_book_total_arb.py — _scan_cross_book_over_under (scanning.py) y los
extractores de total/handicap de Stake/Pinnacle.

Hallazgo de la auditoria: cross_book_total_arb estaba en varias allowlists de
settlement pero NUNCA se generaba realmente -- _scan_cross_book_over_under
era un stub (return []) nunca llamado, y los 6 scrapers cross-book solo
extraian odds de ganador/1X2 (ningun book exponia totales/handicap en el
dict de fixture). Verificado en vivo (2026-06) contra Stake GraphQL y
Pinnacle Guest API que AMBOS ya devuelven los datos de total/handicap en su
unica llamada bulk existente -- solo faltaba parsearlos.

Cada caso se verifica con un ejemplo numerico calculado a mano.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

pytestmark = pytest.mark.skip(
    reason="Total/handicap market extraction is sports-bookie-specific "
    "(Stake/Pinnacle GraphQL/Guest API) with no prediction-market equivalent — "
    "Polymarket/Kalshi/Manifold markets are binary yes/no, not total/handicap "
    "lines. Archived during the prediction-markets pivot (2026-07-03) along "
    "with the underlying sports bookies (src/scrapers/forgotten/)."
)

from src.scrapers.forgotten.stake_scraper import _extract_total_handicap_markets_stake
from src.scrapers.forgotten.pinnacle_scraper import _extract_total_handicap_markets_pinnacle, _american_to_decimal
from src.core.orchestration.scanning import _scan_cross_book_over_under


# ── extractor Stake (formato real verificado en vivo) ──────────────────────

def test_stake_extracts_match_level_total_and_handicap():
    fx = {
        "groups": [{"templates": [{"markets": [
            {"status": "active", "name": "Handicap (Incl. Overtime)", "outcomes": [
                {"name": "Toronto Tempo (-1.5)", "odds": 1.89, "active": True},
                {"name": "L.A. Sparks (1.5)", "odds": 1.89, "active": True},
            ]},
            {"status": "active", "name": "Total (Incl. Overtime)", "outcomes": [
                {"name": "Over 178.5", "odds": 1.79, "active": True},
                {"name": "Under 178.5", "odds": 2.0, "active": True},
            ]},
        ]}]}],
    }
    out = _extract_total_handicap_markets_stake(
        fx, "Toronto Tempo", "L.A. Sparks",
        cross_total_tipos=["total_points"], cross_handicap_tipos=["spread"],
    )
    assert len(out) == 2
    total_m = next(m for m in out if m["tipo"] == "total_points")
    assert total_m == {"tipo": "total_points", "linea": 178.5, "odd_p1": 1.79, "odd_p2": 2.0}
    hcp_m = next(m for m in out if m["tipo"] == "spread")
    assert hcp_m == {"tipo": "spread", "linea": -1.5, "odd_p1": 1.89, "odd_p2": 1.89}


def test_stake_excludes_period_markets_and_quarter_lines():
    """Mercados con prefijo de periodo (1st Quarter, etc.) y lineas de cuarto
    (.25/.75, split settlement no modelado) deben excluirse."""
    fx = {
        "groups": [{"templates": [{"markets": [
            {"status": "active", "name": "1st Quarter - Total", "outcomes": [
                {"name": "Over 45.5", "odds": 1.9, "active": True},
                {"name": "Under 45.5", "odds": 1.9, "active": True},
            ]},
            {"status": "active", "name": "Asian Handicap", "outcomes": [
                {"name": "Toronto Tempo (-1.25)", "odds": 1.95, "active": True},
                {"name": "L.A. Sparks (1.25)", "odds": 1.87, "active": True},
            ]},
        ]}]}],
    }
    out = _extract_total_handicap_markets_stake(
        fx, "Toronto Tempo", "L.A. Sparks",
        cross_total_tipos=["total_points"], cross_handicap_tipos=["spread"],
    )
    assert out == []


# ── extractor Pinnacle (formato real verificado en vivo) ───────────────────

def test_pinnacle_extracts_match_level_total_and_spread():
    mu_markets = [
        {"type": "spread", "period": 0, "prices": [
            {"designation": "home", "points": 11.5, "price": -108},
            {"designation": "away", "points": -11.5, "price": -108},
        ]},
        {"type": "total", "period": 0, "prices": [
            {"designation": "over", "points": 170.0, "price": -109},
            {"designation": "under", "points": 170.0, "price": -111},
        ]},
    ]
    out = _extract_total_handicap_markets_pinnacle(
        mu_markets, cross_total_tipos=["total_points"], cross_handicap_tipos=["spread"],
    )
    assert len(out) == 2
    hcp = next(m for m in out if m["tipo"] == "spread")
    assert hcp["linea"] == 11.5
    assert hcp["odd_p1"] == pytest.approx(_american_to_decimal(-108))
    total = next(m for m in out if m["tipo"] == "total_points")
    assert total["linea"] == 170.0


def test_pinnacle_excludes_period_markets_and_quarter_lines():
    mu_markets = [
        {"type": "spread", "period": 1, "prices": [   # period!=0 -> cuarto/mitad
            {"designation": "home", "points": 5.5, "price": -105},
            {"designation": "away", "points": -5.5, "price": -115},
        ]},
        {"type": "spread", "period": 0, "prices": [   # linea de cuarto
            {"designation": "home", "points": 1.25, "price": -105},
            {"designation": "away", "points": -1.25, "price": -115},
        ]},
    ]
    out = _extract_total_handicap_markets_pinnacle(
        mu_markets, cross_total_tipos=["total_points"], cross_handicap_tipos=["spread"],
    )
    assert out == []


# ── _scan_cross_book_over_under: misma linea + linea cruzada ───────────────

def test_same_line_cross_book_total_arb_detects_real_arb():
    """Stake Over 2.5(2.2) + Pinnacle Under 2.5(2.2), misma linea --
    impl=1/2.2+1/2.2=0.9091, roi=(1-impl)/impl*100=10.0%."""
    raws = [
        {"p1": "A", "p2": "B", "book": "stake", "sport": "football",
         "markets": [{"tipo": "total_goals", "linea": 2.5, "odd_p1": 2.2, "odd_p2": 1.7}]},
        {"p1": "A", "p2": "B", "book": "pinnacle", "sport": "football",
         "markets": [{"tipo": "total_goals", "linea": 2.5, "odd_p1": 1.7, "odd_p2": 2.2}]},
    ]
    opps = _scan_cross_book_over_under(raws, sport="football", min_roi=0.001)
    assert len(opps) == 1
    opp = opps[0]
    assert opp["tipo"] == "cross_book_total_arb"
    assert opp["roi"] == pytest.approx(10.0, abs=1e-3)
    legs_by_book = {l["book"]: l for l in opp["legs"]}
    assert legs_by_book["stake"]["side"] == "over"
    assert legs_by_book["pinnacle"]["side"] == "under"


def test_gapped_line_cross_book_total_arb_detects_real_arb_with_correct_sides():
    """Stake Over 178.5(2.5) + Pinnacle Under 182.5(2.5), lineas distintas
    con gap de cobertura -- impl=1/2.5+1/2.5=0.8, roi=25%. side debe
    remapearse de p1/p2 (convencion interna de scan_cross_over_under) a
    over/under (convencion de settlement de total bets)."""
    raws = [
        {"p1": "Toronto Tempo", "p2": "L.A. Sparks", "book": "stake", "sport": "basketball",
         "markets": [{"tipo": "total_points", "linea": 178.5, "odd_p1": 2.5, "odd_p2": 1.01}]},
        {"p1": "Toronto Tempo", "p2": "L.A. Sparks", "book": "pinnacle", "sport": "basketball",
         "markets": [{"tipo": "total_points", "linea": 182.5, "odd_p1": 1.01, "odd_p2": 2.5}]},
    ]
    opps = _scan_cross_book_over_under(raws, sport="basketball", min_roi=0.001)
    assert len(opps) == 1
    opp = opps[0]
    assert opp["roi"] == pytest.approx(25.0, abs=1e-3)
    assert set(opp["books"]) == {"stake", "pinnacle"}
    assert opp["legs"][0]["side"] == "over"
    assert opp["legs"][1]["side"] == "under"


def test_gapped_handicap_cross_book_keeps_p1_p2_sides():
    """Handicap (a diferencia de total) no tiene over/under -- debe quedar
    p1/p2 sin remapear."""
    raws = [
        {"p1": "Toronto Tempo", "p2": "L.A. Sparks", "book": "stake", "sport": "basketball",
         "markets": [{"tipo": "spread", "linea": 2.5, "odd_p1": 2.5, "odd_p2": 1.01}]},
        {"p1": "Toronto Tempo", "p2": "L.A. Sparks", "book": "pinnacle", "sport": "basketball",
         "markets": [{"tipo": "spread", "linea": -1.5, "odd_p1": 1.01, "odd_p2": 2.5}]},
    ]
    opps = _scan_cross_book_over_under(raws, sport="basketball", min_roi=0.001)
    assert len(opps) == 1
    assert opps[0]["legs"][0]["side"] == "p1"
    assert opps[0]["legs"][1]["side"] == "p2"


def test_no_arb_when_odds_dont_cross():
    """Cuotas normales (sin mispricing) -- no debe reportar nada falso-positivo."""
    raws = [
        {"p1": "A", "p2": "B", "book": "stake", "sport": "football",
         "markets": [{"tipo": "total_goals", "linea": 2.5, "odd_p1": 1.9, "odd_p2": 1.9}]},
        {"p1": "A", "p2": "B", "book": "pinnacle", "sport": "football",
         "markets": [{"tipo": "total_goals", "linea": 2.5, "odd_p1": 1.9, "odd_p2": 1.9}]},
    ]
    opps = _scan_cross_book_over_under(raws, sport="football", min_roi=0.001)
    assert opps == []


def test_single_book_no_markets_produces_nothing():
    """Sin al menos 2 lineas (mismo o distinto book) no hay nada que cruzar."""
    raws = [
        {"p1": "A", "p2": "B", "book": "stake", "sport": "football", "markets": []},
    ]
    opps = _scan_cross_book_over_under(raws, sport="football", min_roi=0.001)
    assert opps == []
