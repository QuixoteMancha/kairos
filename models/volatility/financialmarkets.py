"""
Volatility models for equity-index-underlying binary prediction markets
(S&P 500, NASDAQ 100, Dow Jones — e.g. "will SP500 close above X today").

Same caveat as volatility/crypto.py: no live index-price/ATR feed is
wired into the `market` dict today, so this is a documented placeholder
heuristic, not a real volatility estimate from underlying returns.

Heuristic (same shared shape as other categories, see
volatility/sports.py for full rationale):

    volatility = distance_from_certainty(price) * time_factor(hours_left) * INDEX_VOL_MULTIPLIER

- time_factor also folds in that these markets are closed/dormant
  outside trading hours; TIME_HORIZON_HOURS is scoped to roughly one
  trading session as a placeholder, not a real session-clock model.

TODO: replace INDEX_VOL_MULTIPLIER with real historical/implied
volatility once a live index feed is available (project memory notes
crypto_15m Nasdaq/S&P analog research was blocked on "no free ES/NQ
feed" — same gap applies here).

Fail-closed: missing/invalid prices or missing time-to-resolution => 0.0.
"""

from . import safe_price, safe_hours_to_resolution, distance_from_certainty, clamp01


class _FinancialMarketsVModelBase:
    # TODO: placeholder — roughly one US equity trading session.
    TIME_HORIZON_HOURS = 6.5
    # TODO: placeholder relative-volatility weight, see module docstring.
    INDEX_VOL_MULTIPLIER = 0.6

    def _time_factor(self, hours_left: float) -> float:
        if self.TIME_HORIZON_HOURS <= 0:
            return 0.0
        return clamp01(hours_left / self.TIME_HORIZON_HOURS)

    def calculate_volatility(self, market: dict) -> float:
        price_a = safe_price(market, "current_a_price")
        price_b = safe_price(market, "current_b_price")
        hours_left = safe_hours_to_resolution(market)

        if price_a is None or price_b is None or hours_left is None:
            return 0.0

        uncertainty = (distance_from_certainty(price_a) + distance_from_certainty(price_b)) / 2.0
        return clamp01(uncertainty * self._time_factor(hours_left) * self.INDEX_VOL_MULTIPLIER)


class SP500VModel(_FinancialMarketsVModelBase):
    INDEX_VOL_MULTIPLIER = 0.55


class NASDAQVModel(_FinancialMarketsVModelBase):
    # TODO: tech-heavy index, historically higher relative vol than S&P.
    INDEX_VOL_MULTIPLIER = 0.65


class DOWJVModel(_FinancialMarketsVModelBase):
    # TODO: price-weighted, narrower breadth than S&P/NASDAQ.
    INDEX_VOL_MULTIPLIER = 0.55
