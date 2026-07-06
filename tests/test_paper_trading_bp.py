"""Tests for /api/paper/* endpoints (paper_trading_bp) — SIMULATED money only."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.database.database import init_db
from src.database.models.paper_wallet import SimulatedWallet, SimulatedLedger, SimulatedBet
from src.web.create_app import create_app


@pytest.fixture()
def client():
    init_db()
    SimulatedBet.delete().execute()
    SimulatedLedger.delete().execute()
    SimulatedWallet.delete().execute()
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c
    SimulatedBet.delete().execute()
    SimulatedLedger.delete().execute()
    SimulatedWallet.delete().execute()


def test_wallet_endpoint_seeds_default_200(client):
    resp = client.get("/api/paper/wallet")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["platform"] == "paper"
    assert data["balance"] == 200.0
    assert data["initial_balance"] == 200.0


def test_wallet_endpoint_splits_balance_by_platform_for_display(client):
    resp = client.get("/api/paper/wallet")
    data = resp.get_json()
    by_platform = data["by_platform"]
    assert set(by_platform.keys()) == {"polymarket", "kalshi", "manifold"}
    total = sum(v["balance"] for v in by_platform.values())
    assert total == pytest.approx(200.0, abs=0.01)
    for v in by_platform.values():
        assert v["label"] == "paper"


def test_run_cycle_endpoint_uses_real_scan_but_writes_only_paper_tables(client):
    with patch(
        "src.core.orchestration.scanning._scan_prediction_market_arbs",
        return_value=[],
    ):
        resp = client.post("/api/paper/run-cycle?max_hours=1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["opportunities_scanned"] == 0
    assert data["opened"] == 0


def test_ledger_and_bets_endpoints_empty_wallet(client):
    r1 = client.get("/api/paper/ledger?days=30")
    assert r1.status_code == 200
    assert r1.get_json()["curve"] == []

    r2 = client.get("/api/paper/bets")
    assert r2.status_code == 200
    assert r2.get_json()["bets"] == []
