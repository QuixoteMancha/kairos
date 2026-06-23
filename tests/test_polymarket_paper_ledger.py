"""
test_polymarket_paper_ledger.py — paper trading ledger + PnL realizado de tripletas.

Cubre:
  - realized_triplet_pnl: dual-fill lock, single-fill+hedge-exit, cancelled.
  - polymarket_paper_ledger: registro idempotente, equity curve acumulada,
    summary (ROI/win-rate), histórico, reset.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.orchestration.polymarket_triplet_model import realized_triplet_pnl
from src.core.orchestration import polymarket_paper_ledger as pl


@pytest.fixture(autouse=True)
def _db_clean():
    from src.database.database import init_db
    init_db()
    pl.reset_ledger()
    yield
    pl.reset_ledger()


def _plan(yes=0.47, no=0.47, size=4.0, gross_pnl=0.30, cost=4.5):
    return {
        "pre_event_oco": {"legs": [
            {"outcome": "YES", "price": yes, "size_usd": size},
            {"outcome": "NO", "price": no, "size_usd": size},
        ]},
        "dual_fill_arb": {
            "gross_locked_pnl_usd": gross_pnl,
            "dual_fill_total_cost_usd": cost,
        },
    }


# ── PnL realizado ────────────────────────────────────────────────────────────

def test_pnl_dual_fill_uses_locked_profile():
    out = realized_triplet_pnl(_plan(gross_pnl=0.30, cost=4.5), "dual_fill_locked_arb")
    assert out["pnl_usd"] == 0.30
    assert out["cost_usd"] == 4.5
    assert out["kind"] == "dual_fill_locked_arb"


def test_pnl_single_fill_uses_net_bps():
    # net 50 bps sobre notional 4.0 → 0.005 * 4 = 0.02
    out = realized_triplet_pnl(_plan(size=4.0), "arb_exit_market", net_bps=50.0)
    assert out["pnl_usd"] == round(0.005 * 4.0, 6)
    assert out["cost_usd"] == 4.0


def test_pnl_negative_hedge_exit():
    out = realized_triplet_pnl(_plan(size=4.0), "hedge_exit_market", net_bps=-80.0)
    assert out["pnl_usd"] < 0


def test_pnl_cancelled_is_zero():
    out = realized_triplet_pnl(_plan(), "cancelled")
    assert out["pnl_usd"] == 0.0
    assert out["cost_usd"] == 0.0


# ── Ledger ───────────────────────────────────────────────────────────────────

def test_record_is_idempotent_by_condition_id():
    assert pl.record_resolved_triplet(condition_id="c1", symbol="BTC",
                                      result="dual_fill_locked_arb",
                                      pnl_usd=0.30, cost_usd=4.5, ts=1000) is True
    # mismo condition_id → no duplica
    assert pl.record_resolved_triplet(condition_id="c1", symbol="BTC",
                                      result="dual_fill_locked_arb",
                                      pnl_usd=0.30, cost_usd=4.5, ts=1000) is False
    assert pl.get_summary()["trades"] == 1


def test_summary_aggregates_pnl_and_winrate():
    pl.record_resolved_triplet(condition_id="c1", symbol="BTC", result="dual_fill_locked_arb",
                               pnl_usd=1.0, cost_usd=4.0, ts=1000)
    pl.record_resolved_triplet(condition_id="c2", symbol="BTC", result="hedge_exit_market",
                               pnl_usd=-0.5, cost_usd=4.0, ts=2000)
    s = pl.get_summary()
    assert s["trades"] == 2
    assert s["wins"] == 1
    assert s["losses"] == 1
    assert s["total_pnl_usd"] == 0.5
    assert s["win_rate_pct"] == 50.0
    assert s["equity_usd"] == round(pl.PAPER_SEED_USD + 0.5, 4)


def test_equity_curve_accumulates_from_seed():
    pl.record_resolved_triplet(condition_id="c1", symbol="BTC", result="x",
                               pnl_usd=2.0, cost_usd=4.0, ts=1000)
    pl.record_resolved_triplet(condition_id="c2", symbol="BTC", result="x",
                               pnl_usd=-1.0, cost_usd=4.0, ts=2000)
    curve = pl.get_equity_curve()
    # seed, +2, -1 → [100, 102, 101]
    eqs = [round(p["equity"], 2) for p in curve]
    assert eqs == [pl.PAPER_SEED_USD, pl.PAPER_SEED_USD + 2.0, pl.PAPER_SEED_USD + 1.0]


def test_summary_filters_by_symbol():
    pl.record_resolved_triplet(condition_id="b1", symbol="BTC", result="x",
                               pnl_usd=1.0, cost_usd=4.0, ts=1000)
    pl.record_resolved_triplet(condition_id="e1", symbol="ETH", result="x",
                               pnl_usd=5.0, cost_usd=4.0, ts=1100)
    assert pl.get_summary("BTC")["total_pnl_usd"] == 1.0
    assert pl.get_summary("ETH")["total_pnl_usd"] == 5.0
    assert pl.get_summary()["total_pnl_usd"] == 6.0


def test_reset_clears_ledger():
    pl.record_resolved_triplet(condition_id="c1", symbol="BTC", result="x",
                               pnl_usd=1.0, cost_usd=4.0, ts=1000)
    pl.reset_ledger()
    assert pl.get_summary()["trades"] == 0
    assert pl.recent_trades() == []
