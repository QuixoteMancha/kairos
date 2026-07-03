"""
Regression tests for page/page_size clamping in paginated read endpoints.

Bug: page_size/page came straight from request.args with no bounds — a client
passing page_size=-1 (undefined peewee .paginate() offset behavior) or a huge
page_size (loads the whole table into memory) could crash or DoS the process.
Fixed by clamping page>=1 and 1<=page_size<=200 before use.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    from flask import Flask
    from peewee import SqliteDatabase
    import src.database.database as dbmod

    test_db = SqliteDatabase(":memory:")
    monkeypatch.setattr(dbmod, "db", test_db)
    models = [dbmod.Platform, dbmod.Wallet, dbmod.WalletBet, dbmod.WalletLedger]
    test_db.bind(models, bind_refs=False, bind_backrefs=False)
    test_db.connect()
    test_db.create_tables(models)

    dbmod.Wallet.create(platform="polymarket", initial_balance=50, balance=50, equity=50)

    from src.web.blueprints.audit_bp import audit_bp
    app = Flask(__name__)
    app.register_blueprint(audit_bp)
    yield app.test_client()

    test_db.drop_tables(models)
    test_db.close()


def test_ledger_negative_page_size_is_clamped(client):
    resp = client.get("/api/audit/ledger/polymarket?page_size=-1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["page_size"] >= 1


def test_ledger_huge_page_size_is_clamped(client):
    resp = client.get("/api/audit/ledger/polymarket?page_size=999999999")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["page_size"] <= 200


def test_ledger_negative_page_is_clamped(client):
    resp = client.get("/api/audit/ledger/polymarket?page=-5")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["page"] >= 1


def test_bets_huge_page_size_is_clamped(client):
    resp = client.get("/api/audit/bets/polymarket?page_size=999999999")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["page_size"] <= 200
