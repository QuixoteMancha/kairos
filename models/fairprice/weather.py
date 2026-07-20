# Fair-price model for temperature/weather markets on Kalshi.
#
# Heuristic: simplified climatology. If the market gives us a threshold
# temperature and a historical-average/std-dev for that location+date, use a
# normal-distribution CDF to estimate P(outcome A). This is a deliberately
# simplified rebuild, not a real forecast model (no NWS/live forecast feed
# wired into fairprice/ - that would live in APIs/).
#
# Expected `market` keys:
#   id, type, current_a_price, current_b_price   (base, always required)
#   threshold_f                                   (float, e.g. "above 75F")
#   historical_mean_f, historical_std_f            (float, simplified climatology)
#
# TODO: historical_mean_f / historical_std_f are not currently populated by
# any upstream fetcher. Until wired, this always falls back to market price.

import math

from models.fairprice._common import market_fallback, clamp01


def _normal_cdf(x: float, mean: float, std: float) -> float:
    if std <= 0:
        return 1.0 if x >= mean else 0.0
    z = (x - mean) / (std * math.sqrt(2))
    return 0.5 * (1 + math.erf(z))


class TemperatureFModel:

    def calculate_fair_price(self, market: dict) -> dict:
        threshold = market.get('threshold_f')
        mean = market.get('historical_mean_f')
        std = market.get('historical_std_f')

        if threshold is None or mean is None or std is None:
            return market_fallback(market)

        try:
            threshold = float(threshold)
            mean = float(mean)
            std = float(std)
        except (TypeError, ValueError):
            return market_fallback(market)

        # Assume outcome "A" == "actual temperature exceeds threshold".
        # P(A) = P(X > threshold) = 1 - CDF(threshold)
        fair_a = clamp01(1.0 - _normal_cdf(threshold, mean, std))
        return {'fair_a_price': fair_a, 'fair_b_price': 1.0 - fair_a}
