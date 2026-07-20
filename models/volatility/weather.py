"""
Volatility model for temperature/weather binary prediction markets
(e.g. "will high temp in city X exceed Y degrees by date Z").

Heuristic:

    volatility = distance_from_certainty(price) * time_factor(hours_left)

Same shape as the sports models (see volatility/sports.py for the full
rationale on distance_from_certainty / time_factor), but with a longer
default time horizon since weather forecast uncertainty typically
resolves over a multi-day window rather than a single event's duration.

Fail-closed: missing/invalid prices or missing time-to-resolution => 0.0.
"""

from . import safe_price, safe_hours_to_resolution, distance_from_certainty, clamp01


class TemperatureVModel:
    # TODO: placeholder. Real forecast-uncertainty curves (e.g. NWS
    # ensemble spread by lead time) would replace this flat horizon;
    # forecast uncertainty grows with lead time in a non-linear way that
    # this linear ramp does not capture.
    TIME_HORIZON_HOURS = 72.0

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
