#!/usr/bin/env python3
"""
Tests de la lógica del validador de resolución (scripts/validate_resolution.py).

Valida con datos sintéticos (sin red ni proxy):
  - cobertura de resolución (arb sin 1-won-resto-lost)
  - duplicados mismo jugador/equipo el mismo día y deporte
  - EXCEPCIÓN: tenis singles + dobles el mismo día (jugador puede repetirse)
  - ROI imposible (net P&L > 100% del stake = no es arb bloqueado)
  - mapeo de la pata ganadora a su side (p1/p2)
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_resolution.py"
_spec = importlib.util.spec_from_file_location("validate_resolution", _SCRIPT)
vr = importlib.util.module_from_spec(_spec)
sys.modules["validate_resolution"] = vr
_spec.loader.exec_module(vr)


def _group(gk, fixture, sport, legs, status="resolved", net_pnl=0.0, staked=10.0, gtype="arb_2leg", placed_at="2026-06-15T10:00:00"):
    return {
        "group_key": gk, "group_type": gtype, "fixture_name": fixture,
        "status": status, "net_pnl": net_pnl, "total_staked": staked,
        "placed_at": placed_at,
        "legs": [
            {"status": s, "sport": sport, "market_legs": [{"tipo": "1x2", "side": side}]}
            for s, side in legs
        ],
    }


# ── split / entities ─────────────────────────────────────────────────────────

def test_split_fixture_basic():
    assert vr.split_fixture("Alcaraz vs Sinner") == ("Alcaraz", "Sinner")
    assert vr.split_fixture("Scotland (Corners) vs Morocco (Corners)") == ("Scotland", "Morocco")


def test_is_doubles():
    assert vr.is_doubles("Bopanna/Ebden") is True
    assert vr.is_doubles("Alcaraz") is False


def test_entities_singles_vs_doubles():
    singles = vr.entities("Alcaraz vs Sinner", "tennis")
    assert singles == ["S:alcaraz", "S:sinner"]
    doubles = vr.entities("Bopanna/Ebden vs Arevalo/Pavic", "tennis")
    assert "D:bopanna" in doubles and "D:ebden" in doubles


# ── cobertura ────────────────────────────────────────────────────────────────

def test_coverage_ok_arb():
    g = _group("g1", "A vs B", "tennis", [("won", "p1"), ("lost", "p2")])
    assert vr.check_resolution_coverage([g]) == []


def test_coverage_bad_two_winners():
    g = _group("g1", "A vs B", "tennis", [("won", "p1"), ("won", "p2")])
    bad = vr.check_resolution_coverage([g])
    assert len(bad) == 1


def test_coverage_triplet_ok():
    g = _group("g1", "A vs B", "football", [("won", "p1"), ("lost", "p2"), ("lost", "draw")],
               gtype="triplet_3arb")
    assert vr.check_resolution_coverage([g]) == []


# ── duplicados mismo día ─────────────────────────────────────────────────────

def test_duplicate_same_day_flagged():
    g1 = _group("g1", "Petra Marcinko vs Aoi Ito", "tennis", [("won", "p1"), ("lost", "p2")])
    g2 = _group("g2", "Petra Marcinko vs Renata Zarazua", "tennis", [("lost", "p1"), ("won", "p2")])
    dups = vr.check_duplicate_same_day([g1, g2])
    names = {name for _, name, _, _ in dups}
    assert "petra marcinko" in names


def test_duplicate_different_day_not_flagged():
    g1 = _group("g1", "Marcinko vs Ito", "tennis", [("won", "p1"), ("lost", "p2")],
                placed_at="2026-06-15T10:00:00")
    g2 = _group("g2", "Marcinko vs Zarazua", "tennis", [("lost", "p1"), ("won", "p2")],
                placed_at="2026-06-16T10:00:00")  # día distinto
    assert vr.check_duplicate_same_day([g1, g2]) == []


def test_tennis_singles_plus_doubles_same_day_allowed():
    """EXCEPCIÓN: un jugador en singles Y en dobles el mismo día es válido."""
    g_single = _group("g1", "Bopanna vs Murray", "tennis", [("won", "p1"), ("lost", "p2")])
    g_double = _group("g2", "Bopanna/Ebden vs Arevalo/Pavic", "tennis",
                      [("won", "p1"), ("lost", "p2")])
    dups = vr.check_duplicate_same_day([g_single, g_double])
    names = {name for _, name, _, _ in dups}
    assert "bopanna" not in names  # permitido (single + doble)


def test_two_singles_same_day_same_player_flagged():
    """Dos SINGLES el mismo día con el mismo jugador → sospechoso."""
    g1 = _group("g1", "Bopanna vs Murray", "tennis", [("won", "p1"), ("lost", "p2")])
    g2 = _group("g2", "Bopanna vs Nadal", "tennis", [("lost", "p1"), ("won", "p2")])
    dups = vr.check_duplicate_same_day([g1, g2])
    names = {name for _, name, _, _ in dups}
    assert "bopanna" in names


# ── ROI imposible ────────────────────────────────────────────────────────────

def test_impossible_roi_flagged():
    # arb que devuelve +450% del stake → imposible para arb bloqueado
    g = _group("g1", "A vs B", "tennis", [("won", "p2"), ("lost", "p1")],
               net_pnl=40.95, staked=9.1)
    bad = vr.check_impossible_roi([g])
    assert len(bad) == 1
    assert bad[0][1] > 100  # roi%


def test_normal_arb_roi_not_flagged():
    # arb real: net pequeño (~3% del stake)
    g = _group("g1", "A vs B", "tennis", [("won", "p1"), ("lost", "p2")],
               net_pnl=0.3, staked=10.0)
    assert vr.check_impossible_roi([g]) == []


# ── winner side ──────────────────────────────────────────────────────────────

def test_winner_side_from_group():
    g = _group("g1", "A vs B", "tennis", [("won", "p2"), ("lost", "p1")])
    assert vr.winner_side_from_group(g) == "p2"


# ── arbs donde todas perdieron ───────────────────────────────────────────────

def test_all_legs_lost_flagged():
    g = _group("g1", "A vs B", "tennis", [("lost", "p1"), ("lost", "p2")])
    bad = vr.check_all_legs_lost([g])
    assert len(bad) == 1


def test_normal_arb_not_flagged_as_all_lost():
    g = _group("g1", "A vs B", "tennis", [("won", "p1"), ("lost", "p2")])
    assert vr.check_all_legs_lost([g]) == []


def test_pending_group_not_flagged_as_all_lost():
    g = _group("g1", "A vs B", "tennis", [("lost", "p1"), ("pending", "p2")])
    assert vr.check_all_legs_lost([g]) == []
