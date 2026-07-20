# Internal helpers shared by fairprice/* models. Not part of the public
# import surface used by models/basemodel.py.

def market_fallback(market: dict) -> dict:
    """Fail-closed fallback: no independent edge, mirror market price.

    Used whenever a model lacks the data required to produce a genuine
    independent probability estimate. Coerces current_a_price into [0, 1]
    and defaults to a pure coin-flip (0.5/0.5) only if the market price
    itself is missing/invalid - that is the most neutral, least-harmful
    assumption when there is truly nothing to go on.
    """
    price = market.get('current_a_price')
    if price is None or not isinstance(price, (int, float)) or not (0.0 <= price <= 1.0):
        fair_a = 0.5
    else:
        fair_a = float(price)
    return {'fair_a_price': fair_a, 'fair_b_price': 1.0 - fair_a}


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))
