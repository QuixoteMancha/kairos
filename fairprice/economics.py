# Fair-price models for macroeconomic indicator markets on Kalshi
# (inflation/CPI, unemployment, GDP releases).
#
# Heuristic: consensus-estimate blend. If the market/context supplies an
# economist consensus forecast and a threshold, use a normal-distribution
# CDF around the consensus (with a simplified fixed uncertainty band) to
# estimate P(outcome A). Consensus forecasts (e.g. from a data provider) are
# themselves close to efficient, so this is intentionally conservative.
#
# Expected `market` keys:
#   id, type, current_a_price, current_b_price   (base, always required)
#   threshold                                     (float, e.g. "CPI > 3.0%")
#   consensus_estimate                            (float, survey/consensus forecast)
#   consensus_std                                 (float, optional; dispersion of forecasts)
#
# TODO: no consensus-forecast feed (e.g. Bloomberg/Trading Economics survey
# data) is wired into fairprice/ yet. Until wired, always falls back to
# market price - these releases are highly efficiently priced anyway.

import math

from fairprice._common import market_fallback, clamp01

# Used only if consensus_std is missing: a generic uncertainty band as a
# fraction of the consensus estimate's magnitude. Deliberately crude.
DEFAULT_STD_FRACTION = 0.15


def _normal_cdf(x: float, mean: float, std: float) -> float:
    if std <= 0:
        return 1.0 if x >= mean else 0.0
    z = (x - mean) / (std * math.sqrt(2))
    return 0.5 * (1 + math.erf(z))


class _EconomicsFModel:

    def calculate_fair_price(self, market: dict) -> dict:
        threshold = market.get('threshold')
        consensus = market.get('consensus_estimate')

        if threshold is None or consensus is None:
            return market_fallback(market)

        try:
            threshold = float(threshold)
            consensus = float(consensus)
        except (TypeError, ValueError):
            return market_fallback(market)

        std = market.get('consensus_std')
        try:
            std = float(std) if std is not None else abs(consensus) * DEFAULT_STD_FRACTION
        except (TypeError, ValueError):
            std = abs(consensus) * DEFAULT_STD_FRACTION

        if std <= 0:
            # Degenerate dispersion, can't form a meaningful distribution.
            return market_fallback(market)

        # Assume outcome "A" == "actual reading exceeds threshold".
        fair_a = clamp01(1.0 - _normal_cdf(threshold, consensus, std))
        return {'fair_a_price': fair_a, 'fair_b_price': 1.0 - fair_a}


class InflationFModel(_EconomicsFModel):
    pass


class UnemploymentFModel(_EconomicsFModel):
    pass


class GDPFModel(_EconomicsFModel):
    pass
