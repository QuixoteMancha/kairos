#!/usr/bin/env python3
"""
Tests de los endpoints de capital del wallet (deposit/withdraw/total/reload/cleanup).

Estos endpoints faltaban tras el refactor (el frontend los llamaba → 404).
Se prueban contra el test client de Flask con SQLite en memoria.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from peewee import SqliteDatabase

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.database.database import Platform, Wallet, WalletBet, WalletDeposit, WalletWithdraw, WalletLedger

_MODELS = [Platform, Wallet, WalletBet, WalletDeposit, WalletWithdraw, WalletLedger]


@pytest.fixture
def client():
    test_db = SqliteDatabase(":memory:")
    with test_db.bind_ctx(_MODELS):
        test_db.create_tables(_MODELS)
        Platform.create(slug="stake", name="Stake")
        Wallet.create(platform="stake", initial_balance=50.0, balance=100.0,
                      equity=100.0, deposits=100.0, withdrawals=0.0)
        from src.web.create_app import create_app
        from src.core import state
        state.active_platform = "stake"
        state.wallets.clear()
        app = create_app()
        app.config.update(TESTING=True)
        with app.test_client() as c:
            yield c
        test_db.drop_tables(_MODELS)


def test_deposit_increases_balance(client):
    r1 = client.post("/api/wallet/deposit", json={"amount": 50, "platform": "stake"})
    assert r1.status_code == 200
    b1 = r1.get_json()
    assert b1["ok"] is True
    bal_after_first = b1["balance"]
    dep_after_first = b1["deposits"]
    # Segundo depósito: el balance y deposits deben subir exactamente en amount
    r2 = client.post("/api/wallet/deposit", json={"amount": 25, "platform": "stake"})
    b2 = r2.get_json()
    assert round(b2["balance"] - bal_after_first, 2) == 25.0
    assert round(b2["deposits"] - dep_after_first, 2) == 25.0
    assert WalletDeposit.select().count() == 2


def test_withdraw_decreases_balance(client):
    # Asegurar saldo suficiente primero
    dep = client.post("/api/wallet/deposit", json={"amount": 100, "platform": "stake"}).get_json()
    bal_before = dep["balance"]
    r = client.post("/api/wallet/withdraw", json={"amount": 30, "platform": "stake"})
    assert r.status_code == 200
    body = r.get_json()
    assert round(bal_before - body["balance"], 2) == 30.0
    assert WalletWithdraw.select().count() == 1


def test_withdraw_insufficient_funds(client):
    r = client.post("/api/wallet/withdraw", json={"amount": 9999, "platform": "stake"})
    assert r.status_code == 422
    assert "insuficiente" in r.get_json()["error"].lower()


def test_deposit_rejects_negative(client):
    r = client.post("/api/wallet/deposit", json={"amount": -10, "platform": "stake"})
    assert r.status_code == 422


def test_deposit_rejects_invalid_amount(client):
    r = client.post("/api/wallet/deposit", json={"amount": "abc", "platform": "stake"})
    assert r.status_code == 422


def test_deposit_total_distributes(client):
    r = client.post("/api/wallet/deposit-total", json={"amount": 40})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["total"] == 40


def test_reload_invalidates_cache(client):
    r = client.post("/api/wallet/reload?platform=stake")
    assert r.status_code == 200
    assert r.get_json()["reloaded"] is True


def test_cleanup_keeps_active_wallet(client):
    # Wallet stake tiene deposits>0 → no debe eliminarse
    r = client.post("/api/wallet/cleanup")
    assert r.status_code == 200
    assert "stake" not in r.get_json()["removed"]
    assert Wallet.select().where(Wallet.platform == "stake").count() == 1


# ── Validación de platform (boundary) ────────────────────────────────────────

def test_deposit_rejects_invalid_platform(client):
    r = client.post("/api/wallet/deposit", json={"amount": 10, "platform": "noexiste_xyz"})
    assert r.status_code == 422
    assert "inválida" in r.get_json()["error"].lower()
    # no debe haber creado wallet fantasma
    assert Wallet.select().where(Wallet.platform == "noexiste_xyz").count() == 0


def test_withdraw_rejects_invalid_platform(client):
    r = client.post("/api/wallet/withdraw", json={"amount": 10, "platform": "basura"})
    assert r.status_code == 422


def test_deposit_accepts_registered_platform(client):
    # 'stake' está en Platform → válido
    r = client.post("/api/wallet/deposit", json={"amount": 10, "platform": "stake"})
    assert r.status_code == 200


# ── withdraw-total sin pérdida de céntimos ───────────────────────────────────

def test_withdraw_total_no_cents_lost(client):
    # 3 wallets con balances que fuerzan residuo de redondeo
    Platform.create(slug="unibet", name="Unibet")
    Platform.create(slug="comeon", name="Comeon")
    Wallet.create(platform="unibet", initial_balance=33.33, balance=33.33,
                  equity=33.33, deposits=33.33, withdrawals=0.0)
    Wallet.create(platform="comeon", initial_balance=33.34, balance=33.34,
                  equity=33.34, deposits=33.34, withdrawals=0.0)
    # stake ya tiene balance 100 del fixture
    total_before = round(sum(w.balance for w in Wallet.select()), 2)
    r = client.post("/api/wallet/withdraw-total", json={"amount": 10.0})
    assert r.status_code == 200
    moved = r.get_json()["moved"]
    # la suma de lo retirado debe ser EXACTAMENTE 10.00 (sin céntimos perdidos)
    assert round(sum(m["amount"] for m in moved), 2) == 10.0
    total_after = round(sum(w.balance for w in Wallet.select()), 2)
    assert round(total_before - total_after, 2) == 10.0


# ── reconcile ────────────────────────────────────────────────────────────────

def test_reconcile_recomputes_balance_from_bets(client):
    # Crear una wallet con balance "stale" y una bet resuelta que no cuadra
    WalletBet.create(
        bet_id="b1", platform="stake", placed_at="2026-06-15T10:00:00",
        fixture_name="A vs B", arb_tipo="cross_book_arb_2way", bet_type="cross-arb",
        total_staked=10.0, guaranteed_return=10.2, roi=0.02, status="won", pnl=5.0,
        legs_json="[]",
    )
    # forzar balance incoherente
    w = Wallet.get(Wallet.platform == "stake")
    w.balance = 999.0
    w.save()
    from src.core import state
    state.wallets.pop("stake", None)

    r = client.post("/api/wallet/reconcile", json={"platform": "stake"})
    assert r.status_code == 200
    rec = r.get_json()["reconciled"][0]
    assert rec["platform"] == "stake"
    # balance reconciliado = seed(50) + pnl(5) = 55 (no 999)
    assert rec["balance_after"] != 999.0
    assert rec["balance_after"] == round(50.0 + 5.0, 2)


def test_reconcile_invalid_platform(client):
    r = client.post("/api/wallet/reconcile", json={"platform": "fantasma"})
    assert r.status_code == 422


def test_manual_deposit_survives_wallet_reload(client):
    """
    Regresión (bug crítico): un depósito manual NO debe perderse al recargar la
    wallet. load_or_create reconcilia el balance desde bets+transfers; sin sumar
    WalletDeposit/WalletWithdraw, el depósito desaparecía al revertir al seed.
    """
    # seed=50; depósito de +40 crea WalletDeposit row y sube balance.
    client.post("/api/wallet/deposit", json={"amount": 40, "platform": "stake"})
    # reconcile fuerza recarga desde DB (forget + get_wallet).
    r = client.post("/api/wallet/reconcile", json={"platform": "stake"})
    rec = r.get_json()["reconciled"][0]
    # balance reconciliado = seed(50) + depósito(40) = 90 (NO 50: el depósito vive).
    assert rec["balance_after"] == 90.0


def test_manual_withdraw_reflected_after_reload(client):
    """Un retiro manual también debe persistir tras recargar (seed − retiro)."""
    # Primero asegurar fondos: depósito +60 (balance reconciliado = 50+60=110).
    client.post("/api/wallet/deposit", json={"amount": 60, "platform": "stake"})
    # Retiro de 30 → WalletWithdraw row.
    client.post("/api/wallet/withdraw", json={"amount": 30, "platform": "stake"})
    r = client.post("/api/wallet/reconcile", json={"platform": "stake"})
    rec = r.get_json()["reconciled"][0]
    # seed(50) + dep(60) − wit(30) = 80
    assert rec["balance_after"] == 80.0


