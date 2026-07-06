#!/usr/bin/env python3
"""
Schema tests for the paper-trading (simulated money, NEVER real) models:
SimulatedWallet / SimulatedLedger / SimulatedBet (src/database/models/paper_wallet.py).

Covers:
  1. SimulatedWallet.platform defaults to the literal "paper" (never derived
     from state.active_platform or any other module default).
  2. 3 open+settle cycles across 3 different strategies, writing increasing
     equity_after into SimulatedLedger, and reconstructing a daily equity
     curve by grouping on day_bucket (last equity_after of each day).
  3. Creating/operating on these new tables does not touch the real
     Wallet/WalletLedger tables (row counts before/after are equal).
  4. SimulatedBet.platform_legs stores a JSON list of real platforms
     (polymarket/kalshi/...), distinct from SimulatedWallet.platform="paper".
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _db():
    from src.database.database import init_db
    init_db()
    yield


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _day_bucket(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def test_simulated_wallet_platform_defaults_to_literal_paper():
    from src.database.database import SimulatedWallet

    w = SimulatedWallet.create(
        label=f"paper-default-200usdc-{_now_iso()}",
        initial_balance=200.0,
        balance=200.0,
        equity=200.0,
        started_at=_now_iso(),
    )
    assert w.platform == "paper"
    assert w.initial_balance == 200.0
    assert w.balance == 200.0
    assert w.equity == 200.0
    assert w.strategy_scope == "all"
    assert w.reset_count == 0
    assert w.is_active is True


def test_three_cycles_compounding_equity_curve_by_day_bucket():
    from src.database.database import SimulatedWallet, SimulatedLedger, SimulatedBet

    wallet = SimulatedWallet.create(
        label=f"paper-compounding-test-{_now_iso()}",
        initial_balance=200.0,
        balance=200.0,
        equity=200.0,
        started_at=_now_iso(),
    )

    strategies = [
        "strategy_2_cross_platform",
        "strategy_3_same_market",
        "football_3way",
    ]
    equity = 200.0
    base_day = datetime(2026, 7, 6, tzinfo=timezone.utc)
    expected_last_equity_per_day = {}

    for i, strategy in enumerate(strategies):
        day = base_day  # same day for these 3 cycles, to test "last wins" aggregation
        bucket = _day_bucket(day)
        bet_id = f"sim-bet-{i}"

        bet = SimulatedBet.create(
            bet_id=bet_id,
            wallet=wallet,
            strategy=strategy,
            platform_legs=json.dumps(["polymarket", "kalshi"]),
            opened_at=day.isoformat(),
            total_staked=20.0,
            expected_roi_pct=2.0,
            simulated_fill_json=json.dumps({"fill_pct": 100.0}),
            actual_filled_pct=100.0,
            status="open",
        )

        # open_position: capital committed, balance drops, equity unchanged (mark-to-market)
        SimulatedLedger.create(
            wallet=wallet,
            timestamp=day.isoformat(),
            op_type="open_position",
            ref_table="simulatedbet",
            ref_id=bet_id,
            amount=-20.0,
            balance_after=wallet.balance - 20.0,
            equity_after=equity,
            strategy=strategy,
            day_bucket=bucket,
            note="open",
        )

        # settle_win: compounding - equity grows each cycle
        profit = 5.0 + i  # 5, 6, 7
        equity += profit
        bet.status = "settled_win"
        bet.pnl = profit
        bet.save()

        entry = SimulatedLedger.create(
            wallet=wallet,
            timestamp=day.isoformat(),
            op_type="settle_win",
            ref_table="simulatedbet",
            ref_id=bet_id,
            amount=profit,
            balance_after=equity,
            equity_after=equity,
            strategy=strategy,
            day_bucket=bucket,
            note="settle",
        )
        expected_last_equity_per_day[bucket] = entry.equity_after

        wallet.balance = equity
        wallet.equity = equity
        wallet.save()

    # Reconstruct the daily equity curve: group by day_bucket, take last equity_after.
    rows = list(
        SimulatedLedger.select()
        .where(SimulatedLedger.wallet == wallet)
        .order_by(SimulatedLedger.id.asc())
    )
    assert len(rows) == 6  # 3 opens + 3 settles

    curve = {}
    for row in rows:
        curve[row.day_bucket] = row.equity_after  # last write per bucket wins

    assert curve == expected_last_equity_per_day
    # Compounding actually increased equity vs the 200 USDC anchor.
    assert curve[_day_bucket(base_day)] == pytest.approx(200.0 + 5.0 + 6.0 + 7.0)
    assert wallet.equity == pytest.approx(218.0)


def test_paper_tables_never_touch_real_wallet_tables():
    from src.database.database import (
        Wallet, WalletLedger, SimulatedWallet, SimulatedLedger, SimulatedBet,
    )

    real_wallet_count_before = Wallet.select().count()
    real_ledger_count_before = WalletLedger.select().count()

    wallet = SimulatedWallet.create(
        label=f"paper-isolation-test-{_now_iso()}",
        initial_balance=200.0,
        balance=200.0,
        equity=200.0,
        started_at=_now_iso(),
    )
    SimulatedLedger.create(
        wallet=wallet,
        timestamp=_now_iso(),
        op_type="reset",
        amount=0.0,
        balance_after=200.0,
        equity_after=200.0,
        strategy="strategy_1_universal",
        day_bucket=_day_bucket(datetime.now(timezone.utc)),
        note="isolation check",
    )
    SimulatedBet.create(
        bet_id=f"sim-isolation-{_now_iso()}",
        wallet=wallet,
        strategy="strategy_1_universal",
        platform_legs=json.dumps(["polymarket"]),
        opened_at=_now_iso(),
        total_staked=10.0,
        expected_roi_pct=1.5,
    )

    real_wallet_count_after = Wallet.select().count()
    real_ledger_count_after = WalletLedger.select().count()

    assert real_wallet_count_after == real_wallet_count_before
    assert real_ledger_count_after == real_ledger_count_before


def test_simulated_bet_platform_legs_is_json_of_real_platforms_distinct_from_paper():
    from src.database.database import SimulatedWallet, SimulatedBet

    wallet = SimulatedWallet.create(
        label=f"paper-legs-test-{_now_iso()}",
        initial_balance=200.0,
        balance=200.0,
        equity=200.0,
        started_at=_now_iso(),
    )
    assert wallet.platform == "paper"

    bet = SimulatedBet.create(
        bet_id=f"sim-legs-{_now_iso()}",
        wallet=wallet,
        strategy="strategy_2_cross_platform",
        platform_legs=json.dumps(["polymarket", "kalshi"]),
        opened_at=_now_iso(),
        total_staked=15.0,
        expected_roi_pct=3.2,
    )

    legs = json.loads(bet.platform_legs)
    assert legs == ["polymarket", "kalshi"]
    assert "paper" not in legs
    assert wallet.platform == "paper"
    assert bet.platform_legs != wallet.platform
