# This module contains the YNArb class which controls the logic of the Yes/No Arbitrage strategy.
# It evaluates the risk and return opportunities for each market and executes order placements based on the evaluation of the market current prices
# It looks for the difference between the current prices and if the sum is less than 1 for the two options in a binary market.
#
# A diferencia de LimitArb, esta estrategia NO depende de un modelo de fair
# price/volatilidad por categoría: es un arb "same-market" puro, basado
# solo en los precios yes/no actuales del mismo mercado. Si yes_price +
# no_price < 1 (menos fees), comprar ambas patas garantiza profit sin
# importar el resultado.

from APIs.kalshi import KalshiAPI

MIN_RETURN_OPPORTUNITY = 0.01
# Fee estimada por lado (Kalshi cobra fee por trade, no es 0). Se aplica un
# valor conservador por defecto hasta tener el cálculo real de
# platform_fees para Kalshi; fail-closed: sobreestimar fee subestima
# oportunidades en vez de inflar retorno falso.
ASSUMED_FEE_PER_LEG = 0.01


class YNArb:
    def __init__(self, api: KalshiAPI, markets):
        self.api = api
        self.markets = markets
        self.opportunities = []

    def evaluate_risk_and_return_opportunities(self):
        self.opportunities = []
        for market in self.markets:
            return_opportunity = self._arbitrate(market)
            if return_opportunity is not None and return_opportunity >= MIN_RETURN_OPPORTUNITY:
                self.opportunities.append({
                    'market_id': market['id'],
                    'return_opportunity': return_opportunity,
                })
        return self.opportunities

    def _arbitrate(self, market):
        """Fail-closed: precios ausentes o fuera de [0,1] -> None (no
        oportunidad), nunca se asume un precio por defecto."""
        yes_price = market.get('current_a_price')
        no_price = market.get('current_b_price')

        if yes_price is None or no_price is None:
            return None
        if not (0.0 <= yes_price <= 1.0) or not (0.0 <= no_price <= 1.0):
            return None

        total_cost = yes_price + no_price + (2 * ASSUMED_FEE_PER_LEG)
        if total_cost >= 1.0:
            return None

        # payout garantizado de 1.0 sin importar el outcome, menos lo que
        # costó armar ambas patas (ya con fees incluidas)
        return round(1.0 - total_cost, 4)

    def execute_order_placement(self):
        """NO ejecuta órdenes reales todavía — mismo red-line que LimitArb.
        Worker de solo lectura/propuesta."""
        return self.opportunities
