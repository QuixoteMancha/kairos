"""
Volatility models for crypto-underlying binary prediction markets
(BTC, ETH, SOL, XRP, DOGE, BNB — e.g. "will BTC be above $X at time T").

Ideally this would derive expected probability-price swing from the
underlying asset's realized volatility (e.g. ATR or historical stdev of
returns pulled from a live feed) combined with time-to-resolution and
how far the current implied probability sits from certainty. That live
feed is NOT wired into this package (see TODO below) — market dicts
passed to calculate_volatility are not guaranteed to carry underlying
price/ATR data, only the Kalshi-side probability prices.

Heuristic actually implemented (same shared shape as other categories,
see volatility/sports.py for full rationale):

    volatility = distance_from_certainty(price) * time_factor(hours_left) * ASSET_VOL_MULTIPLIER

- ASSET_VOL_MULTIPLIER is a fail-closed-safe, per-asset placeholder
  weight (<=1.0) reflecting each coin's typical realized-volatility
  ranking relative to BTC (highest-cap, historically lowest relative
  vol among these six). It is NOT derived from a live ATR/stdev feed.

TODO: replace ASSET_VOL_MULTIPLIER with real ATR/historical-volatility
data once a live crypto price feed is wired into the `market` dict
(e.g. `market['underlying_atr']` or similar) — see Binance underlying
usage already established elsewhere in Kairos for crypto_15m (per
project memory: "Binance underlying used, not Kalshi").

Fail-closed: missing/invalid prices or missing time-to-resolution => 0.0.
"""

from . import safe_price, safe_hours_to_resolution, distance_from_certainty, clamp01


class _CryptoVModelBase:
    # TODO: placeholder — crypto markets on Kalshi are often short-dated
    # (hourly/daily), unlike traditional sports/weather; not calibrated.
    TIME_HORIZON_HOURS = 24.0
    # TODO: placeholder relative-volatility weight, see module docstring.
    ASSET_VOL_MULTIPLIER = 1.0

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
        return clamp01(uncertainty * self._time_factor(hours_left) * self.ASSET_VOL_MULTIPLIER)


class BTCVModel(_CryptoVModelBase):
    # TODO: placeholder — largest-cap, treated as relative-vol baseline.
    ASSET_VOL_MULTIPLIER = 0.85


class ETHVModel(_CryptoVModelBase):
    ASSET_VOL_MULTIPLIER = 0.90


class SOLVModel(_CryptoVModelBase):
    # TODO: SOL flagged as the only viable 15m analog per project memory
    # (kairos-newmarkets-investigation-sol15m-highpriority) — likely
    # higher relative vol than BTC/ETH; placeholder only.
    ASSET_VOL_MULTIPLIER = 1.0

class XRPVModel(_CryptoVModelBase):
    ASSET_VOL_MULTIPLIER = 0.95


class DOGEVModel(_CryptoVModelBase):
    # TODO: meme-coin, historically highest relative vol of this set.
    ASSET_VOL_MULTIPLIER = 1.0


class BNBVModel(_CryptoVModelBase):
    ASSET_VOL_MULTIPLIER = 0.85
