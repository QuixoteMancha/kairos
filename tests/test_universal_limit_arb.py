import pytest

from src.core.strategies.universal_limit_arb import (
    compute_l2_max,
    propose_hedge_order,
    propose_initial_orders,
)


def test_compute_l2_max_real_example():
    # L1=0.64, r_target=2% -> L2_max ~= 0.3404 (verified by hand by Sara/Vesper)
    l2_max = compute_l2_max(0.64, 0.02)
    assert l2_max is not None
    assert l2_max == pytest.approx(0.34039, abs=1e-4)


def test_compute_l2_max_fails_closed_when_non_positive():
    # L1 close to 1 with a high r_target pushes L2_max <= 0.
    assert compute_l2_max(0.99, 0.10) is None


def test_compute_l2_max_fails_closed_when_l1_out_of_range():
    assert compute_l2_max(0.0, 0.02) is None
    assert compute_l2_max(1.0, 0.02) is None
    assert compute_l2_max(-0.1, 0.02) is None
    assert compute_l2_max(1.1, 0.02) is None


def test_compute_l2_max_fails_closed_when_negative_r_target():
    assert compute_l2_max(0.64, -0.01) is None


def test_compute_l2_max_ge_one_case():
    # Very low L1 with r_target = 0 would push l2_max close to 1 but still < 1;
    # construct a case where it is >= 1 is not mathematically possible for valid
    # r_target >= 0 and l1 > 0 (l2_max = 1/(1+r) - l1 < 1 always when l1>0, r>=0).
    # Instead verify boundary near l1 -> 0 stays valid (sanity, not a None case).
    l2_max = compute_l2_max(0.001, 0.0)
    assert l2_max is not None
    assert l2_max < 1.0


def test_propose_hedge_order_real_example_never_exceeds_l2_max():
    # The real trade: L1=0.64, N=10, side_b_current_price=0.30, r_target=2%.
    # L2_max = 0.3404. The order that was actually placed at 0.38 would have been
    # invalid (ROI -1.96% if filled there) -- this proposal must never reach 0.38.
    result = propose_hedge_order(l1=0.64, n=10, side_b_current_price=0.30, r_target=0.02)
    assert result is not None
    assert result["price"] <= 0.3404
    assert result["price"] != pytest.approx(0.38, abs=1e-6)
    assert result["quantity"] == 10
    assert result["roi_if_filled_here"] >= 0.02 - 1e-9


def test_propose_hedge_order_returns_none_when_price_already_past_ceiling():
    # side_b_current_price (0.38) > l2_max (0.3404) -- no viable hedge, fail closed.
    result = propose_hedge_order(l1=0.64, n=10, side_b_current_price=0.38, r_target=0.02)
    assert result is None


def test_propose_hedge_order_invalid_inputs_fail_closed():
    assert propose_hedge_order(l1=0.64, n=0, side_b_current_price=0.30) is None
    assert propose_hedge_order(l1=0.64, n=-5, side_b_current_price=0.30) is None
    assert propose_hedge_order(l1=0.64, n=10, side_b_current_price=1.5) is None
    assert propose_hedge_order(l1=0.64, n=10, side_b_current_price=0.30, k=1.5) is None


def test_propose_hedge_order_k_extremes():
    # k=0 -> price == current market price (most conservative).
    result_k0 = propose_hedge_order(l1=0.64, n=10, side_b_current_price=0.30, k=0.0)
    assert result_k0 is not None
    assert result_k0["price"] == pytest.approx(0.30, abs=1e-9)

    # k=1 -> price == l2_max (most aggressive allowed, still safe).
    result_k1 = propose_hedge_order(l1=0.64, n=10, side_b_current_price=0.30, k=1.0)
    assert result_k1 is not None
    assert result_k1["price"] == pytest.approx(0.34039, abs=1e-4)


def test_propose_initial_orders_basic():
    levels = propose_initial_orders(price_a=0.48, price_b=0.52, quantity=10)
    assert levels is not None
    assert levels.side_a_price == pytest.approx(0.51)
    assert levels.side_b_price == pytest.approx(0.55)
    assert levels.quantity == 10


def test_propose_initial_orders_fails_closed_out_of_range():
    assert propose_initial_orders(price_a=0.99, price_b=0.5, quantity=10, epsilon=0.03) is None
    assert propose_initial_orders(price_a=0.5, price_b=0.5, quantity=0) is None
    assert propose_initial_orders(price_a=1.5, price_b=0.5, quantity=10) is None
