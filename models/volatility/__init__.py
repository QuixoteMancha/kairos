"""
volatility/ — Volatility models for Kairos limit-arb / dwell-time strategy.

A "volatility model" estimates how much a Kalshi prediction-market price
(a probability in [0.0, 1.0]) might oscillate during the remaining window
before the market resolves. This is NOT asset-return volatility (no sigma
of log-returns) — it's "how much can the implied probability move before
resolution", used as a filter: low expected volatility => not worth
placing a limit order hoping to capture price oscillation.

Design rules shared by every model in this package:

- Fail-closed: any missing/invalid input (None price, price outside
  [0,1], missing time-to-resolution, etc.) returns 0.0 (no volatility)
  rather than guessing high. Missing information must never make an
  arb opportunity look more attractive.
- Output is always a float in [0.0, 1.0] representing an expected
  price-range/oscillation magnitude on the 0-1 probability scale.
- Every class exposes the same interface:
      def calculate_volatility(self, market: dict) -> float
- `market` dict is expected to carry at least:
      'id', 'type', 'current_a_price', 'current_b_price'
  and, where used, 'time_to_resolution_hours' (float, hours until the
  market resolves). Any other key is category-specific and is read
  defensively via market.get(...) with a documented fallback.
"""

from typing import Optional


def safe_price(market: dict, key: str) -> Optional[float]:
    """
    Extract a price field and validate it is a float in [0.0, 1.0].
    Returns None (never raises) if missing, wrong type, or out of range,
    so callers can fail-closed to 0.0 volatility.
    """
    value = market.get(key)
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if value < 0.0 or value > 1.0:
        return None
    return value


def safe_hours_to_resolution(market: dict, key: str = "time_to_resolution_hours") -> Optional[float]:
    """
    Extract remaining time to resolution in hours.
    Returns None if missing, non-numeric, or negative.
    """
    value = market.get(key)
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if value < 0.0:
        return None
    return value


def distance_from_certainty(price: float) -> float:
    """
    How far a price sits from a "settled" market (0 or 1), peaking at
    price == 0.5. Scaled to [0, 1] via 4 * p * (1-p) (max at p=0.5 -> 1.0).
    Rationale: a price near 0.5 reflects genuine uncertainty and has more
    room to swing in either direction before resolution than a price
    already near 0 or 1, which is comparatively "priced in".
    """
    return max(0.0, min(1.0, 4.0 * price * (1.0 - price)))


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))
