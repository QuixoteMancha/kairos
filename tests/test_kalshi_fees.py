from outer_apis.kalshi.fees import order_fee


def test_fee_exact_cent_is_not_overrounded_by_binary_float_noise():
    # 0.04 * 1 * 0.50 * (1 - 0.50) is exactly one cent.
    assert order_fee(0.50, contracts=1, fee_multiplier=0.04) == 0.01


def test_fee_supports_fractional_contracts_and_rejects_invalid_values():
    assert order_fee(0.65, contracts=1 / 0.65, fee_multiplier=0.07) == 0.03
    assert order_fee("bad", contracts=1) is None
    assert order_fee(0.5, contracts="bad") is None
