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
