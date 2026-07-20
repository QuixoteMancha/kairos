# Fair-price models for crypto price-threshold markets on Kalshi
# (BTC/ETH/SOL/XRP/DOGE/BNB).
#
# Heuristic: crypto spot/derivatives markets are highly liquid and close to
# informationally efficient at short/medium horizons, so the deliberate
# default here is "no independent edge" - the model trusts the current
# market price as the best available estimate (a random-walk assumption),
# rather than trying to out-predict price direction with an ad hoc formula.
# This intentionally avoids resurrecting any of the old momentum/technical
# models. If a genuine external edge source (e.g. an options-implied-vol
# based threshold model) is added later, it should replace this fallback,
# not bolt onto it.
#
# Expected `market` keys:
#   id, type, current_a_price, current_b_price   (base, always required)
#
# TODO: no independent probability source (e.g. options-implied distribution
# from Deribit) is wired into fairprice/ yet. Always falls back to market
# price by design (efficient-market assumption), not just missing data.

from fairprice._common import market_fallback


class _CryptoFModel:

    def calculate_fair_price(self, market: dict) -> dict:
        # By design: no edge model, market price treated as fair (see module
        # docstring). Uses the same fail-closed helper as the missing-data
        # case elsewhere, since the outcome is identical either way.
        return market_fallback(market)


class BTCFModel(_CryptoFModel):
    pass


class ETHFModel(_CryptoFModel):
    pass


class SOLFModel(_CryptoFModel):
    pass


class XRPFModel(_CryptoFModel):
    pass


class DOGEFModel(_CryptoFModel):
    pass


class BNBFModel(_CryptoFModel):
    pass
