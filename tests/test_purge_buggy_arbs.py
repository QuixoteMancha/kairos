#!/usr/bin/env python3
"""
Tests del endpoint de purga de arbs afectados por el bug de reparto de stake.

POST /api/bets/purge-buggy-arbs
  - dry_run=True (default): solo previsualiza los grupos con ROI imposible
    (P&L neto > threshold × stake) sin tocar nada.
  - dry_run=False: elimina TODAS las patas de esos grupos y revierte su
    impacto en el balance (resuelta → balance -= pnl).

Un arb bloqueado real tiene ROI de pocos %; los afectados por el bug 50/50
muestran 130-450% → se marcan; un arb sano y un single quedan intactos.
"""
from __future__ import annotations

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

        # ARB CON BUG: reparto 50/50 → underdog ganó +40.95, favorito perdió -4.55
        # net = +36.40 sobre stake 9.10 → ROI 400% (imposible para arb real)
        _bet("bug1", "stake",    "won",  4.55, 40.95, "cross_book_arb_2way",
             "2026-06-15T10:00:00", combo_key="cb_BUG")
        _bet("bug2", "pinnacle", "lost", 4.55, -4.55, "cross_book_arb_2way",
             "2026-06-15T10:00:00", combo_key="cb_BUG")

        # ARB SANO: stake repartido inverso a cuotas → net pequeño (+0.40 sobre 10) = 4%
        _bet("ok1", "stake",    "won",  9.0, 1.40, "cross_book_arb_2way",
             "2026-06-15T11:00:00", combo_key="cb_OK")
        _bet("ok2", "pinnacle", "lost", 1.0, -1.00, "cross_book_arb_2way",
             "2026-06-15T11:00:00", combo_key="cb_OK")

        # SINGLE direccional (no es arb, nunca debe marcarse aunque gane mucho)
        _bet("sng", "stake", "won", 5.0, 50.0, "stat", "2026-06-15T12:00:00", bet_type="stat")

        # PENDIENTE: stake comprometido (balance ya lo descuenta vía reconciliación)
        _bet("pend1", "pinnacle", "pending", 7.0, 0.0, "cross_book_arb_2way",
             "2026-06-15T13:00:00", combo_key="cb_PEND")

        from src.web.create_app import create_app
        from src.core import state
        state.active_platform = "stake"
        state.wallets.clear()
        state.deleted_bet_ids.clear()
        state.fixture_url_map = {}
        app = create_app()
        app.config.update(TESTING=True)
        with app.test_client() as c:
            yield c, test_db
        test_db.drop_tables(_MODELS)


# ── DRY RUN ──────────────────────────────────────────────────────────────────

def test_dry_run_flags_only_buggy_arb(client):
    c, _ = client
    j = c.post("/api/bets/purge-buggy-arbs", json={"dry_run": True}).get_json()
    assert j["dry_run"] is True
    assert j["summary"]["groups_flagged"] == 1
    assert j["summary"]["bets_flagged"] == 2
    g = j["groups"][0]
    assert g["group_key"] == "cb_BUG"
    assert g["roi_pct"] == 400.0
    assert set(g["bet_ids"]) == {"bug1", "bug2"}


def test_dry_run_does_not_delete(client):
    c, _ = client
    c.post("/api/bets/purge-buggy-arbs", json={"dry_run": True})
    assert WalletBet.select().count() == 6  # nada borrado


def test_default_is_dry_run(client):
    c, _ = client
    j = c.post("/api/bets/purge-buggy-arbs", json={}).get_json()
    assert j["dry_run"] is True
    assert WalletBet.select().count() == 6


def test_healthy_arb_and_single_not_flagged(client):
    c, _ = client
    j = c.post("/api/bets/purge-buggy-arbs", json={"dry_run": True}).get_json()
    flagged_keys = {g["group_key"] for g in j["groups"]}
    assert "cb_OK" not in flagged_keys
    assert "single:sng" not in flagged_keys


# ── DELETE REAL ──────────────────────────────────────────────────────────────

def test_real_delete_removes_buggy_legs(client):
    c, _ = client
    j = c.post("/api/bets/purge-buggy-arbs", json={"dry_run": False}).get_json()
    assert j["dry_run"] is False
    assert j["deleted_count"] == 2
    assert set(j["deleted_bet_ids"]) == {"bug1", "bug2"}
    remaining = {b.bet_id for b in WalletBet.select()}
    assert remaining == {"ok1", "ok2", "sng", "pend1"}


def test_real_delete_reverts_balance(client):
    c, _ = client
    # La wallet en-memoria reconstruye el balance desde las bets resueltas;
    # al purgar bug1/bug2 su P&L inflado desaparece del cálculo.
    # stake = 100 + ok1(+1.40) + sng(+50) = 151.40 (bug1 +40.95 eliminado)
    # pinnacle = 100 + ok2(-1.00) − pend1_stake(7) = 92.00 (bug2 -4.55 eliminado)
    c.post("/api/bets/purge-buggy-arbs", json={"dry_run": False})
    stake_w = Wallet.get(Wallet.platform == "stake")
    pinn_w  = Wallet.get(Wallet.platform == "pinnacle")
    assert stake_w.balance == round(100.0 + 1.40 + 50.0, 2)
    assert pinn_w.balance == round(100.0 - 1.00 - 7.0, 2)


def test_threshold_can_widen_selection(client):
    c, _ = client
    # threshold muy bajo (0.0) marca cualquier arb con net positivo → cb_OK también
    j = c.post("/api/bets/purge-buggy-arbs",
               json={"dry_run": True, "threshold": 0.0}).get_json()
    flagged = {g["group_key"] for g in j["groups"]}
    assert "cb_BUG" in flagged
    assert "cb_OK" in flagged


# ── ANTI-RESURRECCIÓN (tombstone + WAL) ──────────────────────────────────────

def test_purge_registers_tombstones(client):
    c, _ = client
    from src.core import state
    c.post("/api/bets/purge-buggy-arbs", json={"dry_run": False})
    # las patas borradas quedan marcadas como tombstone para esta sesión
    assert {"bug1", "bug2"} <= state.deleted_bet_ids


def test_deleted_bet_not_resurrected_by_stale_save(client):
    """
    Reproduce el bug recurrente: un worker conserva una VirtualWallet en memoria
    con la bet ya borrada y llama save() → antes la re-insertaba (INSERT OR IGNORE
    no bloquea filas inexistentes). Con el tombstone, save() la omite.
    """
    c, _ = client
    from src.core import state
    from src.core.math.simulation import VirtualWallet

    # Cargar una wallet stale ANTES de borrar (simula worker con referencia viva)
    stale = VirtualWallet.load_or_create(platform="stake")
    assert any(b.bet_id == "bug1" for b in stale.bets)

    # Purga real → borra bug1/bug2 de DB y registra tombstones
    c.post("/api/bets/purge-buggy-arbs", json={"dry_run": False})
    assert WalletBet.get_or_none(WalletBet.bet_id == "bug1") is None

    # El worker stale persiste su estado: NO debe resucitar bug1
    stale.save()
    assert WalletBet.get_or_none(WalletBet.bet_id == "bug1") is None
    assert WalletBet.get_or_none(WalletBet.bet_id == "bug2") is None


# ── BORRADO MASIVO POR STATUS (pendientes) ───────────────────────────────────

def test_delete_by_status_dry_run_counts_pending(client):
    c, _ = client
    j = c.post("/api/bets/delete-by-status",
               json={"status": "pending", "dry_run": True}).get_json()
    assert j["dry_run"] is True
    assert j["summary"]["bets_flagged"] == 1
    assert j["summary"]["by_platform"]["pinnacle"]["count"] == 1
    # nada borrado en dry_run
    assert WalletBet.get_or_none(WalletBet.bet_id == "pend1") is not None


def test_delete_by_status_default_is_dry_run(client):
    c, _ = client
    j = c.post("/api/bets/delete-by-status", json={"status": "pending"}).get_json()
    assert j["dry_run"] is True
    assert WalletBet.get_or_none(WalletBet.bet_id == "pend1") is not None


def test_delete_by_status_removes_pending(client):
    c, _ = client
    j = c.post("/api/bets/delete-by-status",
               json={"status": "pending", "dry_run": False}).get_json()
    assert j["deleted_count"] == 1
    assert j["deleted_bet_ids"] == ["pend1"]
    assert WalletBet.get_or_none(WalletBet.bet_id == "pend1") is None


def test_delete_pending_restores_balance(client):
    c, _ = client
    # pinnacle: balance reconciliado = 100 + ok2(-1) + bug2(-4.55) − pend1_stake(7) = 87.45
    pinn_before = Wallet.get(Wallet.platform == "pinnacle")
    # forzar reconciliación previa cargando la wallet
    c.post("/api/bets/delete-by-status",
           json={"status": "pending", "dry_run": False})
    pinn_after = Wallet.get(Wallet.platform == "pinnacle")
    # al borrar la pendiente, su stake (7) deja de restar → balance sube 7
    # balance final = 100 + ok2(-1) + bug2(-4.55) = 94.45
    assert pinn_after.balance == round(100.0 - 1.00 - 4.55, 2)


def test_delete_by_status_invalid_rejected(client):
    c, _ = client
    r = c.post("/api/bets/delete-by-status", json={"status": "garbage"})
    assert r.status_code == 400


