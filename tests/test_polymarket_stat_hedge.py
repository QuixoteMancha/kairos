from src.core.orchestration.polymarket_stat_hedge import (
    CostModel,
    StatHedgeAction,
    decide_live_stat_hedge,
    roundtrip_bps,
)


def test_roundtrip_bps_profit_after_costs():
    costs = CostModel(entry_fee_bps=0, exit_fee_bps=25, exit_slippage_bps=10)
    gross, cost, net = roundtrip_bps(0.45, 0.50, costs)
    assert gross > 0
    assert cost == 35
    assert net == gross - cost


def test_decide_arb_exit_when_net_above_threshold():
    d = decide_live_stat_hedge(
        entry_price=0.40,
        live_exit_price=0.43,
        costs=CostModel(entry_fee_bps=0, exit_fee_bps=20, exit_slippage_bps=10),
        min_net_arb_bps=30,
        stop_loss_bps=35,
    )
    assert d.action == StatHedgeAction.ARB_EXIT_MARKET


def test_decide_hedge_exit_when_stop_hit():
    d = decide_live_stat_hedge(
        entry_price=0.52,
        live_exit_price=0.49,
        costs=CostModel(entry_fee_bps=0, exit_fee_bps=25, exit_slippage_bps=10),
        min_net_arb_bps=20,
        stop_loss_bps=30,
    )
    assert d.action == StatHedgeAction.HEDGE_EXIT_MARKET


def test_decide_hold_inside_band():
    d = decide_live_stat_hedge(
        entry_price=0.50,
        live_exit_price=0.501,
        costs=CostModel(entry_fee_bps=0, exit_fee_bps=20, exit_slippage_bps=10),
        min_net_arb_bps=40,
        stop_loss_bps=40,
    )
    assert d.action == StatHedgeAction.HOLD
