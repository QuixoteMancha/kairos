#!/usr/bin/env python3
"""
Regression tests: state.active_platform == 'cross_book' is the internal
unified-engine mode marker, NOT a real platform — it must never be used as
an implicit default to instantiate/persist a wallet, nor resurrect the old
'stake' sports-bookie slug (purged from production).

Covers the critical findings from the 2026-07 whole-project bug hunt:
  1. POST /api/wallet/set-platform with slug='cross_book' must not create
     or touch a 'stake' (or any) wallet row.
  2. POST /api/circuit-breaker/reset and POST /api/settle must operate on
     the platform given explicitly in the request body, never silently on
     a 'cross_book' phantom wallet.

Uses the shared test DB (see conftest.py, DB_PATH redirected to a temp file
for the whole session) via init_db(), same pattern as
test_rebalance_pause_guard.py — unique platform slugs avoid collisions with
other test modules sharing the same DB file.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _db_and_state():
    from src.database.database import init_db
    from src.core import state
    init_db()
    prev_active = state.active_platform
    state.active_platform = "cross_book"
    state.wallets.clear()
    yield state
    state.active_platform = prev_active
    state.wallets.clear()


@pytest.fixture
def app_client():
    from src.web.create_app import create_app
    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as c:
        yield c


def _seed_wallet(slug: str, **overrides):
    from src.database.database import Platform, Wallet
    Platform.get_or_create(slug=slug, defaults={"name": slug.title(), "ptype": "prediction_market"})
    row = Wallet.get_or_none(Wallet.platform == slug)
    if row is not None:
        row.delete_instance()
    defaults = dict(initial_balance=50.0, balance=100.0, equity=100.0, deposits=50.0, withdrawals=0.0)
    defaults.update(overrides)
    Wallet.create(platform=slug, **defaults)


def _cleanup_wallet(slug: str):
    from src.database.database import Wallet, WalletBet
    Wallet.delete().where(Wallet.platform == slug).execute()
    WalletBet.delete().where(WalletBet.platform == slug).execute()


def test_set_platform_cross_book_does_not_resurrect_stake(app_client):
    from src.database.database import Wallet

    stake_existed_before = Wallet.get_or_none(Wallet.platform == "stake") is not None

    r = app_client.post("/api/wallet/set-platform", json={"platform": "cross_book"})
    assert r.status_code == 200
    assert r.get_json()["active_platform"] == "cross_book"

    # The endpoint must not have created a 'stake' wallet as a side effect of
    # activating the internal 'cross_book' mode marker.
    stake_exists_after = Wallet.get_or_none(Wallet.platform == "stake") is not None
    assert stake_exists_after == stake_existed_before


def test_settle_uses_explicit_platform_not_cross_book(app_client):
    from src.database.database import Wallet, WalletBet

    slug = "test_pm_settle"
    _seed_wallet(slug)
    WalletBet.create(
        bet_id="bet-settle-1", platform=slug, placed_at=datetime.now().isoformat(),
        fixture_name="Will X happen", arb_tipo="inside-arb", total_staked=10.0,
        guaranteed_return=10.5, roi=5.0, status="pending", resolution_scope="match",
    )
    try:
        r = app_client.post("/api/settle", json={"platform": slug})
        assert r.status_code == 200
        body = r.get_json()
        assert "bet-settle-1" in body["settled"]

        bet = WalletBet.get(WalletBet.bet_id == "bet-settle-1")
        assert bet.status == "won"

        # No phantom 'cross_book' wallet should have been created by this call.
        assert Wallet.get_or_none(Wallet.platform == "cross_book") is None
    finally:
        _cleanup_wallet(slug)


def test_circuit_breaker_reset_requires_and_uses_explicit_platform(app_client):
    from src.database.database import Wallet

    slug = "test_pm_cb"
    _seed_wallet(slug, balance=100.0, equity=100.0, deposits=100.0)
    try:
        r = app_client.post("/api/circuit-breaker/reset", json={"platform": slug, "new_peak": 150.0})
        assert r.status_code == 200
        body = r.get_json()
        assert body["platform"] == slug
        assert body["new_peak"] == 150.0
        # Must reflect this wallet's real equity, not an empty 'cross_book' one.
        assert body["equity"] > 0
        assert Wallet.get_or_none(Wallet.platform == "cross_book") is None
    finally:
        _cleanup_wallet(slug)


def test_circuit_breaker_reset_without_platform_is_rejected(app_client):
    r = app_client.post("/api/circuit-breaker/reset", json={"new_peak": 150.0})
    assert r.status_code in (400, 422)


def test_delete_bet_adjusts_correct_platform_not_active_platform(app_client):
    """
    DELETE /api/bet/<bet_id> must adjust the balance of the bet's OWN
    platform, never state.active_platform. Here active_platform is
    'cross_book' (an internal mode marker, not a real wallet) while the bet
    actually belongs to a distinct real platform ('test_pm_delete'). Without
    the fix, get_wallet()/save_wallet() would fall back to active_platform
    and either touch the wrong wallet or resurrect a phantom 'cross_book'
    one; the real platform's balance would never be credited back.
    """
    from src.database.database import Wallet, WalletBet
    from src.core import state

    slug = "test_pm_delete"
    _seed_wallet(slug, balance=40.0, equity=50.0)
    assert state.active_platform == "cross_book"

    WalletBet.create(
        bet_id="bet-delete-1", platform=slug, placed_at=datetime.now().isoformat(),
        fixture_name="Will Y happen", arb_tipo="inside-arb", total_staked=10.0,
        guaranteed_return=10.5, roi=5.0, status="pending", resolution_scope="match",
    )
    try:
        r = app_client.delete("/api/bet/bet-delete-1")
        assert r.status_code == 200
        body = r.get_json()

        # The pending stake must be refunded to the bet's real platform.
        w_row = Wallet.get(Wallet.platform == slug)
        assert w_row.balance == pytest.approx(50.0)
        assert body["balance"] == pytest.approx(50.0)

        # No phantom 'cross_book' wallet should have been created/touched.
        assert Wallet.get_or_none(Wallet.platform == "cross_book") is None

        # Bet must actually be gone.
        assert WalletBet.get_or_none(WalletBet.bet_id == "bet-delete-1") is None
    finally:
        _cleanup_wallet(slug)
