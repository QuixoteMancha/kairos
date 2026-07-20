# Fair-price models for equity index markets on Kalshi (S&P 500, Nasdaq 100,
# Dow Jones).
#
# Heuristic: same rationale as fairprice/crypto.py - major equity indices are
# highly liquid, heavily arbitraged (futures/options/ETFs), and close to
# informationally efficient over the short horizons Kalshi markets on these
# indices typically cover. Default is "no independent edge": trust the
# current market price (random-walk assumption). Not resurrecting the old
# technical/momentum architecture.
#
# Expected `market` keys:
#   id, type, current_a_price, current_b_price   (base, always required)
#
# TODO: no independent probability source (e.g. options-implied distribution
# from index options chains) is wired into fairprice/ yet. Always falls back
# to market price by design (efficient-market assumption), not just missing
# data.

from models.fairprice._common import market_fallback


class _FinancialMarketFModel:

    def calculate_fair_price(self, market: dict) -> dict:
        # By design: no edge model, market price treated as fair (see module
        # docstring).
        return market_fallback(market)


class SP500FModel(_FinancialMarketFModel):
    pass


class NASDAQFModel(_FinancialMarketFModel):
    pass


class DOWJFModel(_FinancialMarketFModel):
    pass
