"""
test_bets_export_roi_coherence.py — coherencia stake/ROI en historial y pendientes.

Bug reportado: "el stake apostado total de los arbs vs lo que muestra cada pata
(la suma) y el ROI final del arb a veces no coinciden".

Causa: cada pata cross-book guarda el `roi` del ARB COMPLETO repetido y en escala
MIXTA (a veces fracción 0.03, a veces porcentaje 3.0), sin un ROI de grupo
coherente. El export ahora expone:
  - group.total_staked == Σ leg.total_staked  (invariante)
  - group.net_pnl      == Σ leg.pnl (resueltas)
  - group.roi_expected_pct  (normalizado a escala única)
  - group.roi_realized_pct  == net_pnl / total_staked * 100  (solo resuelto)
  - leg.roi_pct            (normalizado por pata)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from peewee import SqliteDatabase

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.database.database import (
    Platform, Wallet, WalletBet, WalletDeposit, WalletWithdraw, WalletLedger,
)

_MODELS = [Platform, Wallet, WalletBet, WalletDeposit, WalletWithdraw, WalletLedger]


def _bet(bet_id, plat, status, stake, pnl, tipo, ts, **kw):
    return WalletBet.create(
        bet_id=bet_id, platform=plat, placed_at=ts, fixture_name=kw.get("fixture", f"Fix {bet_id}"),
        arb_tipo=tipo, bet_type=kw.get("bet_type", ""), total_staked=stake,
        guaranteed_return=stake * 1.02, roi=kw.get("roi", 0.02), status=status, pnl=pnl,
        combo_key=kw.get("combo_key", ""), source_bet_id=kw.get("source_bet_id", ""),
        hedge_bet_id=kw.get("hedge_bet_id", ""), is_hedge=kw.get("is_hedge", False),
        source_group_key=kw.get("source_group_key", ""), legs_json=kw.get("legs_json", "[]"),
    )


@pytest.fixture
def client():
    test_db = SqliteDatabase(":memory:")
    with test_db.bind_ctx(_MODELS):
        test_db.create_tables(_MODELS)
        Platform.create(slug="stake", name="Stake")
        Platform.create(slug="pinnacle", name="Pinnacle")
        Wallet.create(platform="stake", initial_balance=100.0, balance=100.0,
                      equity=100.0, deposits=0.0, withdrawals=0.0)
        Wallet.create(platform="pinnacle", initial_balance=100.0, balance=100.0,
                      equity=100.0, deposits=0.0, withdrawals=0.0)

        # ARB RESUELTO con roi en escala FRACCIÓN (0.03). Reparto inverso a cuotas:
        # stake 9.0 + 1.0 = 10.0 total; net = +0.40 → realizado 4%.
        _bet("r1", "stake", "won", 9.0, 0.90, "cross_book_arb_2way",
             "2026-06-15T10:00:00", combo_key="cb_RES", roi=0.03)
        _bet("r2", "pinnacle", "lost", 1.0, -0.50, "cross_book_arb_2way",
             "2026-06-15T10:00:00", combo_key="cb_RES", roi=0.03)

        # ARB con roi en escala PORCENTAJE (3.0 == 3%) → debe normalizar a 3.0.
        _bet("p1", "stake", "won", 8.0, 1.0, "cross_book_arb_2way",
             "2026-06-15T11:00:00", combo_key="cb_PCT", roi=3.0)
        _bet("p2", "pinnacle", "lost", 2.0, -2.0, "cross_book_arb_2way",
             "2026-06-15T11:00:00", combo_key="cb_PCT", roi=3.0)

        # ARB PENDIENTE (no debe traer roi_realized).
        _bet("pend1", "stake", "pending", 5.0, 0.0, "cross_book_arb_2way",
             "2026-06-15T12:00:00", combo_key="cb_PEND", roi=0.025)
        _bet("pend2", "pinnacle", "pending", 5.0, 0.0, "cross_book_arb_2way",
             "2026-06-15T12:00:00", combo_key="cb_PEND", roi=0.025)

        # ARB con una pata VOID: el stake void se devuelve (no estuvo en riesgo).
        # won 10€ pnl +0.50 ; void 10€ pnl 0 → ROI real = 0.50/10 = 5% (NO 0.50/20=2.5%)
        _bet("v1", "stake", "won", 10.0, 0.50, "cross_book_arb_2way",
             "2026-06-15T13:00:00", combo_key="cb_VOID", roi=0.05)
        _bet("v2", "pinnacle", "void", 10.0, 0.0, "cross_book_arb_2way",
             "2026-06-15T13:00:00", combo_key="cb_VOID", roi=0.05)

        from src.web.create_app import create_app
        from src.core import state
        state.active_platform = "stake"
        state.wallets.clear()
        app = create_app()
        app.config.update(TESTING=True)
        with app.test_client() as c:
            yield c
        test_db.drop_tables(_MODELS)


def _group(j, key):
    return next(g for g in j["groups"] if g["group_key"] == key)


# ── INVARIANTE: total_staked == Σ patas ──────────────────────────────────────

def test_group_total_staked_equals_sum_of_legs(client):
    j = client.get("/api/bets/export?format=json").get_json()
    for g in j["groups"]:
        leg_sum = round(sum(l["total_staked"] for l in g["legs"]), 2)
        assert g["total_staked"] == leg_sum, f"{g['group_key']} stake no cuadra"


def test_group_net_pnl_equals_sum_of_resolved_legs(client):
    j = client.get("/api/bets/export?format=json").get_json()
    g = _group(j, "cb_RES")
    leg_pnl = round(sum(l["pnl"] for l in g["legs"] if l["status"] in ("won", "lost")), 2)
    assert g["net_pnl"] == leg_pnl


# ── ROI realizado coherente con el dinero ────────────────────────────────────

def test_roi_realized_matches_money(client):
    j = client.get("/api/bets/export?format=json").get_json()
    g = _group(j, "cb_RES")
    # net +0.40 sobre 10.0 = 4.0%
    assert g["net_pnl"] == 0.40
    assert g["total_staked"] == 10.0
    assert g["roi_realized_pct"] == 4.0


def test_roi_expected_normalized_regardless_of_scale(client):
    j = client.get("/api/bets/export?format=json").get_json()
    # cb_RES guardó roi=0.03 (fracción) y cb_PCT guardó roi=3.0 (porcentaje):
    # ambos deben exponer roi_expected_pct == 3.0
    assert _group(j, "cb_RES")["roi_expected_pct"] == 3.0
    assert _group(j, "cb_PCT")["roi_expected_pct"] == 3.0


def test_leg_roi_pct_normalized(client):
    j = client.get("/api/bets/export?format=json").get_json()
    for l in _group(j, "cb_PCT")["legs"]:
        assert l["roi_pct"] == 3.0   # 3.0 (pct) normalizado → 3.0


# ── PENDIENTES: sin roi_realized ─────────────────────────────────────────────

def test_pending_group_has_no_realized_roi(client):
    j = client.get("/api/bets/export?format=json&status=pending").get_json()
    g = _group(j, "cb_PEND")
    assert g["status"] == "pending"
    assert g["roi_realized_pct"] is None
    # pero el stake total sí debe cuadrar con las patas
    assert g["total_staked"] == round(sum(l["total_staked"] for l in g["legs"]), 2)


def test_csv_includes_roi_columns(client):
    txt = client.get("/api/bets/export?format=csv").get_data(as_text=True)
    header = txt.splitlines()[0]
    assert "group_roi_expected_pct" in header
    assert "group_roi_realized_pct" in header
    assert "leg_roi_pct" in header


def test_roi_realized_excludes_void_stake(client):
    """El stake de patas void se devuelve → no diluye el ROI realizado."""
    j = client.get("/api/bets/export?format=json").get_json()
    g = _group(j, "cb_VOID")
    assert g["status"] == "resolved"
    # net +0.50 sobre el capital EXPUESTO (solo la pata won, 10€) = 5.0%
    # (NO 0.50/20 = 2.5% que incluiría la pata void)
    assert g["net_pnl"] == 0.50
    assert g["roi_realized_pct"] == 5.0

