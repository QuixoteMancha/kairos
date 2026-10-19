# This module contains the MarketArb class which controls the logic of the Yes/No and Option A/Option B Arbitrage strategy.
# It evaluates the risk and return opportunities for each market and executes order placements based on the evaluation of the market current prices
# It looks for the difference between the current prices and if the sum is less than 1 for the two options in a binary market.
#
# A diferencia de LimitArb, esta estrategia NO depende de un modelo de fair
# price/volatilidad por categoría: es un arb "same-market" puro, basado
# solo en los precios actuales del mismo mercado (aplica tanto a mercados
# yes/no como a option-A/option-B). Si el costo de armar ambas patas
# (precio_a + precio_b + fees) es menor a 1, comprar AMBAS patas con
# MARKET ORDER garantiza profit sin importar el resultado.
#
# Ambas patas se ejecutan como market order, en el mismo instante, no hay
# pata límite (a diferencia de LimitArb). El riesgo real de esta estrategia
# es de TIMING: el edge solo es capturable si el sistema alcanza a colocar
# las dos órdenes antes de que el book se mueva (latencia/slippage entre
# la lectura del precio y la ejecución real). Todavía no hay wiring de
# ejecución real de órdenes (línea roja del proyecto) — por ahora esto
# reporta el plan de dos patas market, no las coloca.
#
# Dos variantes del mismo arb, ambas cubiertas por esta clase sin lógica
# adicional (mismo shape de market dict: current_a_price/current_b_price):
#
#   1. SAME-TICKET: un solo ticker de Kalshi cuyo yes/no son complementarios
#      dentro del mismo mercado (ej. la mayoría de mercados binarios). Acá
#      ambas patas se colocan en el MISMO ticker (yes y no).
#
#   2. CROSS-TICKET: dos tickers DISTINTOS de Kalshi que son complementarios
#      entre sí porque describen el mismo evento desde lados opuestos —
#      hallazgo real verificado en tenis: cada partido ATP/WTA en Kalshi son
#      DOS mercados separados ("¿Gana el jugador A?" / "¿Gana el jugador B?"),
#      no un solo ticker A/B (ver outer_apis/kalshi_tennis_markets.py). Si
#      precio_yes_A + precio_yes_B + fees < 1, comprar "yes" en AMBOS
#      tickers garantiza cobrar $1 sin importar quién gane — mismo arb,
#      dos tickers distintos. Acá cada pata se coloca en su propio ticker
#      (`ticker_a`/`ticker_b` en el market dict, ver `_build_order_plan`).
#
# El market dict distingue el caso vía `ticker_a`/`ticker_b` (si están
# presentes, cross-ticket; si no, ambas patas caen en `market['id']`, el
# ticker del propio mercado — same-ticket, comportamiento histórico).

from outer_apis.kalshi.client import KalshiAPI
from outer_apis.kalshi.fees import order_fee

MIN_RETURN_OPPORTUNITY = 0.01


class MarketArb:
    def __init__(self, api: KalshiAPI, markets):
        self.api = api
        self.markets = markets
        self.opportunities = []

    def evaluate_risk_and_return_opportunities(self):
        self.opportunities = []
        for market in self.markets:
            plan = self._build_order_plan(market)
            if plan is not None and plan['return_opportunity'] >= MIN_RETURN_OPPORTUNITY:
                self.opportunities.append({
                    'market_id': market['id'],
                    'return_opportunity': plan['return_opportunity'],
                    'market_leg_a': plan['market_leg_a'],
                    'market_leg_b': plan['market_leg_b'],
                })
        return self.opportunities

    def _build_order_plan(self, market):
        """Fail-closed: precios ausentes o fuera de [0,1] -> None (no
        oportunidad), nunca se asume un precio por defecto. `price_a`/
        `price_b` son genéricos: side 'a' es yes u option-A según el
        mercado, side 'b' es no u option-B. Fees reales de Kalshi (ver
        outer_apis/kalshi_fees.py) para ambas patas, ambas market/taker.

        `ticker_a`/`ticker_b`: opcionales en el market dict. Si están
        presentes (caso cross-ticket, ej. tenis: dos tickers distintos de
        Kalshi complementarios entre sí) cada pata lleva su propio ticker
        de ejecución. Si no están (caso same-ticket, comportamiento
        histórico) ambas patas caen en `market['id']`, el ticker del
        propio mercado."""
        price_a = market.get('current_a_price')
        price_b = market.get('current_b_price')
        category = market.get('type')

        if price_a is None or price_b is None:
            return None
        if not (0.0 <= price_a <= 1.0) or not (0.0 <= price_b <= 1.0):
            return None
        # Fees are non-negative.  Avoid a series-metadata request for a
        # market that is already impossible before fees, which is by far the
        # common case in a broad scanner.
        if price_a + price_b >= 1.0:
            return None

        fee_kwargs = {"category": category}
        series_ticker = market.get("series_ticker")
        if series_ticker and isinstance(self.api, KalshiAPI):
            # A cross-ticket/same-ticket arb cannot be called net-positive
            # until its series multiplier is known. Fill-level rounding and
            # rebates are still modeled conservatively by ``order_fee``.
            # The category estimate remains a compatibility fallback for
            # legacy markets lacking a series.
            try:
                metadata = self.api.get_series_fee_metadata(series_ticker)
            except Exception:
                return None
            if metadata is None:
                return None
            fee_kwargs["fee_multiplier"] = metadata["fee_multiplier"]

        fee_a = order_fee(price_a, **fee_kwargs)
        fee_b = order_fee(price_b, **fee_kwargs)
        if fee_a is None or fee_b is None:
            # fail-closed: sin dato de fee confiable, no hay oportunidad
            return None

        total_cost = price_a + price_b + fee_a + fee_b
        if total_cost >= 1.0:
            return None

        ticker_a = market.get('ticker_a', market.get('id'))
        ticker_b = market.get('ticker_b', market.get('id'))
        # In a single binary ticker, leg B is the complementary NO side.
        # In a paired event (ATP/WTA), ticker_b already asks whether player
        # B wins, so the complementary claim is YES on *both* tickers.
        # Keeping ``side='b'`` for that second ticker produced a plan whose
        # economics did not match the guaranteed-payout calculation.
        side_b = 'a' if ticker_a != ticker_b else 'b'

        # payout garantizado de 1.0 sin importar el outcome, menos lo que
        # costó armar ambas patas (ya con fees incluidas). Ambas patas se
        # ejecutan como market order en el mismo instante.
        return {
            'return_opportunity': round(1.0 - total_cost, 4),
            'market_leg_a': {'ticker': ticker_a, 'side': 'a', 'action': 'market', 'price': price_a},
            'market_leg_b': {'ticker': ticker_b, 'side': side_b, 'action': 'market', 'price': price_b},
        }

    def execute_order_placement(self):
        """NO ejecuta órdenes reales todavía — mismo red-line que LimitArb.
        Worker de solo lectura/propuesta."""
        return self.opportunities
