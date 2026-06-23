#!/usr/bin/env python3
"""
Tests del export de historial de bets (con relaciones de grupo) y de la curva
de equity reconstruida.

Cubre:
  - /api/bets/export?format=json  → agrupa por combo_key (2-arb), 3way (triplet),
    stat + hedge (par stat-live-hedge), y singles.
  - /api/bets/export?format=csv   → filas planas con columnas de grupo.
  - /api/wallet/equity-history    → curva reconstruida desde bets (no plana en 0).
"""
from __future__ import annotations

import csv as _csv
import io as _io
import sys
from pathlib import Path

import pytest
from peewee import SqliteDatabase

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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

        # 2-arb: dos patas del mismo combo
        _bet("a1", "stake", "won", 10.0, 4.0, "cross_book_arb_2way", "2026-06-15T10:00:00", combo_key="cb_AAA")
        _bet("a2", "pinnacle", "lost", 10.0, -10.0, "cross_book_arb_2way", "2026-06-15T10:00:00", combo_key="cb_AAA")
        # 3-arb (triplet): tres patas
        _bet("t1", "stake", "won", 8.0, 2.0, "cross_book_arb_3way", "2026-06-15T11:00:00", combo_key="cb_BBB")
        _bet("t2", "pinnacle", "lost", 8.0, -8.0, "cross_book_arb_3way", "2026-06-15T11:00:00", combo_key="cb_BBB")
        _bet("t3", "stake", "won", 8.0, 6.0, "cross_book_arb_3way", "2026-06-15T11:00:00", combo_key="cb_BBB")
        # stat + hedge: el hedge enlaza con el source
        _bet("s1", "stake", "won", 5.0, 3.0, "stat", "2026-06-15T12:00:00", bet_type="stat", combo_key="cb_CCC")
        _bet("h1", "pinnacle", "lost", 3.0, -3.0, "stat-hedge", "2026-06-15T12:30:00",
             bet_type="stat-hedge", is_hedge=True, source_bet_id="s1")
        # single suelto
        _bet("x1", "stake", "pending", 6.0, 0.0, "cross_book_arb_2way", "2026-06-15T13:00:00")

        from src.web.create_app import create_app
        from src.core import state
        state.active_platform = "stake"
        state.wallets.clear()
        state.fixture_url_map = {}
        app = create_app()
        app.config.update(TESTING=True)
        with app.test_client() as c:
            yield c
        test_db.drop_tables(_MODELS)


# ── EXPORT JSON ──────────────────────────────────────────────────────────────

def test_export_json_groups_2arb_by_combo(client):
    j = client.get("/api/bets/export?format=json").get_json()
    groups = {g["group_key"]: g for g in j["groups"]}
    assert "cb_AAA" in groups
    g = groups["cb_AAA"]
    assert g["n_legs"] == 2
    assert g["group_type"] == "arb_2leg"
    # net pnl del grupo = 4 - 10 = -6
    assert g["net_pnl"] == -6.0
    assert {leg["bet_id"] for leg in g["legs"]} == {"a1", "a2"}


def test_export_json_groups_triplet_3arb(client):
    j = client.get("/api/bets/export?format=json").get_json()
    g = next(g for g in j["groups"] if g["group_key"] == "cb_BBB")
    assert g["n_legs"] == 3
    assert g["group_type"] == "triplet_3arb"
    assert g["net_pnl"] == round(2.0 - 8.0 + 6.0, 2)


def test_export_json_links_stat_hedge_pair(client):
    """El hedge (source_bet_id=s1) debe quedar en el grupo del stat origen."""
    j = client.get("/api/bets/export?format=json").get_json()
    # s1 tiene combo_key cb_CCC; h1 enlaza por source_bet_id → mismo grupo
    g = next(g for g in j["groups"] if g["group_key"] == "cb_CCC")
    leg_ids = {leg["bet_id"] for leg in g["legs"]}
    assert {"s1", "h1"}.issubset(leg_ids)
    assert g["group_type"] == "stat_live_hedge"


def test_export_json_summary_consistent(client):
    j = client.get("/api/bets/export?format=json").get_json()
    s = j["summary"]
    assert s["bets_total"] == 8
    # net pnl global == suma de net_pnl de grupos
    assert s["net_pnl"] == round(sum(g["net_pnl"] for g in j["groups"]), 2)


def test_export_json_filter_by_status(client):
    j = client.get("/api/bets/export?format=json&status=pending").get_json()
    all_legs = [leg for g in j["groups"] for leg in g["legs"]]
    assert all(leg["status"] == "pending" for leg in all_legs)
    assert any(leg["bet_id"] == "x1" for leg in all_legs)


# ── EXPORT CSV ───────────────────────────────────────────────────────────────

def test_export_csv_has_group_columns(client):
    r = client.get("/api/bets/export?format=csv")
    assert r.status_code == 200
    assert "text/csv" in r.content_type
    assert "attachment" in r.headers.get("Content-Disposition", "")
    rows = list(_csv.reader(_io.StringIO(r.get_data(as_text=True))))
    header = rows[0]
    assert "group_key" in header and "group_type" in header and "leg_bet_id" in header
    # una fila por leg: 8 bets → 8 filas de datos
    assert len(rows) - 1 == 8


def test_export_csv_preserves_relationships(client):
    r = client.get("/api/bets/export?format=csv")
    rows = list(_csv.DictReader(_io.StringIO(r.get_data(as_text=True))))
    # a1 y a2 comparten group_key
    a1 = next(x for x in rows if x["leg_bet_id"] == "a1")
    a2 = next(x for x in rows if x["leg_bet_id"] == "a2")
    assert a1["group_key"] == a2["group_key"] == "cb_AAA"
    assert a1["group_type"] == "arb_2leg"


# ── CURVA DE EQUITY reconstruida ─────────────────────────────────────────────

def test_equity_history_not_flat_zero(client):
    """La curva NO debe ser plana en 0 (bug de equity_after siempre 0)."""
    j = client.get("/api/wallet/equity-history?period=all&platform=all").get_json()
    pts = j["points"]
    assert len(pts) >= 2
    equities = [p["equity"] for p in pts]
    # seed = 200; debe haber valores != 0
    assert any(e != 0.0 for e in equities)
    assert j["seed"] == 200.0


def test_equity_history_anchored_to_seed(client):
    """El primer punto parte del seed; la curva acumula el pnl realizado."""
    j = client.get("/api/wallet/equity-history?period=all&platform=all").get_json()
    pts = j["points"]
    assert pts[0]["equity"] == 200.0  # seed inicial
    # pnl realizado total: 4-10 +2-8+6 +3-3 = -6 ; equity final = 200 - 6 = 194
    assert pts[-1]["equity"] == pytest.approx(194.0, abs=0.01)


def test_equity_history_platform_filter(client):
    """Filtro por plataforma reconstruye solo el seed+pnl de esa casa."""
    j = client.get("/api/wallet/equity-history?period=all&platform=stake").get_json()
    assert j["seed"] == 100.0  # solo stake
    # stake pnl realizado: a1 +4, t1 +2, t3 +6, s1 +3 = +15 → equity 115
    assert j["points"][-1]["equity"] == pytest.approx(115.0, abs=0.01)
