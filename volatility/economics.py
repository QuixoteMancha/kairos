"""
Volatility models for macroeconomic-release binary prediction markets
(inflation/CPI, unemployment, GDP prints).

These markets are dominated by a small number of scheduled data-release
events (e.g. monthly CPI print) rather than continuous news flow, so
volatility is modeled as concentrated near the release: distance-from-
certainty scaled by a time factor with a *short* horizon relative to
sports/weather, reflecting that price barely moves until the print date
and then can jump sharply right around it.

Heuristic (same shared shape as sports/weather — see volatility/sports.py
for full rationale):

    volatility = distance_from_certainty(price) * time_factor(hours_left)

Fail-closed: missing/invalid prices or missing time-to-resolution => 0.0.
"""

from . import safe_price, safe_hours_to_resolution, distance_from_certainty, clamp01


class _EconomicsVModelBase:
    # TODO: placeholder. Real calibration should use the historical
    # price-jump distribution around past releases of the same series
    # (e.g. BLS CPI print surprise magnitude), not a flat hours-based ramp.
    TIME_HORIZON_HOURS = 24.0

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
        return clamp01(uncertainty * self._time_factor(hours_left))


class InflationVModel(_EconomicsVModelBase):
    # TODO: placeholder — CPI print cadence is monthly; 24h horizon only
    # covers the immediate pre/post-release window, not the full month.
    TIME_HORIZON_HOURS = 24.0


class UnemploymentVModel(_EconomicsVModelBase):
    # TODO: placeholder — NFP/unemployment print cadence is monthly.
    TIME_HORIZON_HOURS = 24.0


class GDPVModel(_EconomicsVModelBase):
    # TODO: placeholder — GDP print cadence is quarterly; revisions add
    # extra volatility events this model does not account for.
    TIME_HORIZON_HOURS = 24.0
