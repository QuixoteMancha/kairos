"""Tests for the manual paper-wallet reset (fixes the "wallet stuck negative
from the 2026-07-07 indeterminate-stake-limbo bug" real-world scenario the
user hit in production) -- src/core/orchestration/paper_trading_cycle.reset_wallet()
and POST /api/paper/wallet/reset."""
from __future__ import annotations

from src.database.database import init_db
from src.database.models.paper_wallet import SimulatedWallet, SimulatedLedger, SimulatedBet
from src.core.orchestration import paper_trading_cycle as cycle
from src.web.create_app import create_app

import pytest


@pytest.fixture(autouse=True)
def _clean_paper_tables():
    init_db()
    SimulatedBet.delete().execute()
    SimulatedLedger.delete().execute()
    SimulatedWallet.delete().execute()
    yield
    SimulatedBet.delete().execute()
    SimulatedLedger.delete().execute()
    SimulatedWallet.delete().execute()


def test_reset_restores_balance_and_increments_reset_count():
    w = cycle.get_or_create_wallet("reset-wallet-1")
    w.balance = -0.44
    w.equity = -0.44
    w.save()

    result = cycle.reset_wallet("reset-wallet-1", initial_balance=200.0)
    assert result["reset"] is True
    assert result["reset_count"] == 1
    assert result["old_balance"] == -0.44

    w2 = SimulatedWallet.get(SimulatedWallet.label == "reset-wallet-1")
    assert w2.balance == 200.0
    assert w2.equity == 200.0
    assert w2.reset_count == 1


def test_reset_writes_ledger_entry():
    cycle.get_or_create_wallet("reset-wallet-2")
    cycle.reset_wallet("reset-wallet-2", initial_balance=200.0)
    w = SimulatedWallet.get(SimulatedWallet.label == "reset-wallet-2")
    entries = list(SimulatedLedger.select().where(
        (SimulatedLedger.wallet == w) & (SimulatedLedger.op_type == "reset")
    ))
    assert len(entries) == 1


def test_reset_preserves_old_bets_but_they_dont_affect_new_balance():
    w = cycle.get_or_create_wallet("reset-wallet-3")
    SimulatedBet.create(bet_id="old-bet-1", wallet=w, strategy="strategy_1_universal",
                        status="strategy1_indeterminate", pnl=0.0)
    cycle.reset_wallet("reset-wallet-3", initial_balance=200.0)
    # Old bet still queryable (audit trail preserved)
    assert SimulatedBet.get_or_none(SimulatedBet.bet_id == "old-bet-1") is not None
    w2 = SimulatedWallet.get(SimulatedWallet.label == "reset-wallet-3")
    assert w2.balance == 200.0


def test_reset_nonexistent_wallet_fails_closed():
    result = cycle.reset_wallet("does-not-exist", initial_balance=200.0)
    assert result["reset"] is False
    assert result["reason"] == "wallet_not_found"


def test_purge_deletes_bets_and_ledger_rows():
    w = cycle.get_or_create_wallet("reset-wallet-purge")
    SimulatedBet.create(bet_id="purge-bet-1", wallet=w, strategy="strategy_1_universal",
                        status="strategy1_indeterminate", pnl=0.0)
    SimulatedBet.create(bet_id="purge-bet-2", wallet=w, strategy="strategy_1_universal",
                        status="strategy1_resolved_arb", pnl=1.5)
    cycle._write_ledger(w, "open_position", -5.0, "strategy_1_universal", note="test")

    result = cycle.reset_wallet("reset-wallet-purge", initial_balance=200.0, archive_bets=False)
    assert result["purged"] is True
    assert result["deleted_bets"] == 2
    assert result["deleted_ledger_rows"] == 1  # the pre-existing open_position row

    assert SimulatedBet.select().where(SimulatedBet.wallet == w).count() == 0
    # Only the fresh "reset" ledger entry written AFTER the purge should remain
    remaining = list(SimulatedLedger.select().where(SimulatedLedger.wallet == w))
    assert len(remaining) == 1
    assert remaining[0].op_type == "reset"

    w2 = SimulatedWallet.get(SimulatedWallet.label == "reset-wallet-purge")
    assert w2.balance == 200.0


def test_purge_endpoint():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        client.get("/api/paper/wallet?label=purge-endpoint-wallet")
        w = SimulatedWallet.get(SimulatedWallet.label == "purge-endpoint-wallet")
        SimulatedBet.create(bet_id="purge-endpoint-bet", wallet=w, strategy="strategy_1_universal",
                            status="strategy1_observing")
        resp = client.post("/api/paper/wallet/reset", json={"label": "purge-endpoint-wallet", "purge": True})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["purged"] is True
        assert data["deleted_bets"] == 1
        assert SimulatedBet.select().where(SimulatedBet.wallet == w).count() == 0


def test_reset_endpoint():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        client.get("/api/paper/wallet?label=reset-wallet-4")  # seeds it
        resp = client.post("/api/paper/wallet/reset", json={"label": "reset-wallet-4"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["reset"] is True
        assert data["new_balance"] == 200.0
