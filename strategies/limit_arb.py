# This module contains the LimitArb class which controls the logic of the Limit Arbitrage strategy.
# It evaluates the risk and return opportunities for each market and executes order placements based on the evaluation
# The LimitArb consists in a market order and a limit order, where the limit order is placed at a price that is more favorable than the current market price, and the market order is placed at the current market price.
# The goal is to take advantage of the difference between the two prices to make a profit.
# Also the LimitArb strategy can have the market order executed in-event to get a better current price which maximize the profit.
# The LimitArb can be formed with yes/no pair and also with option a/option b pair (Player A/Player B, Team A/Team B, etc).

from APIs.kalshi import KalshiAPI
from models.basemodel import Model

MIN_VOLATILITY = 0.10
MIN_RETURN_OPPORTUNITY = 0.02


class LimitArb:
    def __init__(self, api: KalshiAPI, markets):
        self.api = api
        self.markets = markets
        self.model = Model()
        self.opportunities = []

    def evaluate_risk_and_return_opportunities(self):
        """Recorre todos los mercados y junta las oportunidades que superan
        el umbral mínimo de volatilidad esperada y de retorno. No corta en
        la primera oportunidad encontrada (bug del borrador original: el
        `return` dentro del for solo evaluaba el primer mercado)."""
        self.opportunities = []
        for market in self.markets:
            avg_volatility = self.model.calculate_average_volatility(market)
            return_opportunity = self._arbitrate(market, avg_volatility)

            if avg_volatility > MIN_VOLATILITY and return_opportunity >= MIN_RETURN_OPPORTUNITY:
                self.opportunities.append({
                    'market_id': market['id'],
                    'avg_volatility': avg_volatility,
                    'return_opportunity': return_opportunity,
                })

        return self.opportunities

    def _arbitrate(self, market, avg_volatility):
        """Compara el precio de mercado contra el fair price del modelo.
        Fail-closed: si el modelo no tiene edge (fair == market, caso por
        defecto de Model.calculate_fair_price sin modelo de categoría),
        devuelve 0 — ninguna oportunidad fantasma."""
        fair_price = self.model.calculate_fair_price(market)

        current_a = market.get('current_a_price')
        current_b = market.get('current_b_price')
        fair_a = fair_price.get('fair_a_price')
        fair_b = fair_price.get('fair_b_price')

        if None in (current_a, current_b, fair_a, fair_b):
            return 0

        if current_a > fair_a:
            # el mercado sobrevalora la pata A: comprar B es la oportunidad
            return (1 - current_a - avg_volatility) + fair_b
        elif current_b > fair_b:
            return (1 - current_b - avg_volatility) + fair_a
        else:
            return 0

    def execute_order_placement(self):
        """NO ejecuta órdenes reales todavía — Kairos no tiene wiring de
        auth/firma para colocación de órdenes (línea roja del proyecto,
        requiere autorización explícita del usuario paso a paso). Por ahora
        esto es un worker de solo lectura/propuesta: reporta las
        oportunidades encontradas."""
        return self.opportunities
