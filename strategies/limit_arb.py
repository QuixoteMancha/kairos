# This module contains the LimitArb class which controls the logic of the Limit Arbitrage strategy.
# It evaluates the risk and return opportunities for each market and executes order placements based on the evaluation
# The LimitArb consists in a market order and a limit order, where the limit order is placed at a price that is more favorable than the current market price, and the market order is placed at the current market price.
# The goal is to take advantage of the difference between the two prices to make a profit.
# Also the LimitArb strategy can have the market order executed in-event to get a better current price which maximize the profit.
# The LimitArb can be formed with yes/no pair and also with option a/option b pair (Player A/Player B, Team A/Team B, etc).
#
# Lógica de dos patas (NO es un detector de edge puro): en Kalshi las dos
# patas de un mercado binario son complementarias, current_a_price +
# current_b_price == 1 (menos spread). Se identifica la "favorita" (la
# pata con precio actual más alto) y se compara contra su fair price:
#
#   Caso 1 — favorita SOBREVALORADA (fav_price > fav_fair):
#     se espera que la favorita CAIGA hacia su fair price, y por
#     complementariedad la no-favorita SUBA. Entonces:
#       - MARKET order en la no-favorita, ahora, mientras está barata.
#       - LIMIT order en la favorita, al precio actual menos la
#         volatilidad promedio esperada (se espera capturarla en la caída).
#
#   Caso 2 — favorita INFRAVALORADA (fav_price < fav_fair):
#     se espera que la favorita SUBA hacia su fair price, y la
#     no-favorita CAIGA. Entonces:
#       - MARKET order en la favorita, ahora, mientras está barata.
#       - LIMIT order en la no-favorita, al precio actual menos la
#         volatilidad promedio esperada (se espera capturarla en la caída).
#
# El edge (|fav_price - fav_fair|) neto de fees reales de Kalshi (ambas
# patas, ver APIs/kalshi_fees.py) es el filtro de umbral mínimo
# (MIN_RETURN_OPPORTUNITY) y avg_volatility sigue siendo el filtro mínimo de
# que valga la pena montar una pata límite (MIN_VOLATILITY) — pero el
# resultado que importa para operar es el plan de dos patas, no el edge.

from APIs.kalshi import KalshiAPI
from APIs.kalshi_fees import order_fee
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
            plan = self._build_order_plan(market, avg_volatility)

            if plan is None:
                continue

            if avg_volatility > MIN_VOLATILITY and plan['return_opportunity'] >= MIN_RETURN_OPPORTUNITY:
                self.opportunities.append({
                    'market_id': market['id'],
                    'avg_volatility': avg_volatility,
                    'return_opportunity': plan['return_opportunity'],
                    'case': plan['case'],
                    'market_leg': plan['market_leg'],
                    'limit_leg': plan['limit_leg'],
                })

        return self.opportunities

    def _build_order_plan(self, market, avg_volatility):
        """Identifica la favorita (mayor precio actual) vs la no-favorita,
        compara contra el fair price del modelo, y arma el plan de las dos
        patas (market + limit) según el caso.

        Fail-closed: si falta cualquier dato necesario, o el modelo no
        reporta edge (fair == market, caso por defecto de
        Model.calculate_fair_price sin modelo de categoría), no hay plan
        (None) — nunca se inventa una oportunidad por ausencia de datos."""
        fair_price = self.model.calculate_fair_price(market)

        current_a = market.get('current_a_price')
        current_b = market.get('current_b_price')
        fair_a = fair_price.get('fair_a_price')
        fair_b = fair_price.get('fair_b_price')

        if None in (current_a, current_b, fair_a, fair_b):
            return None

        if current_a >= current_b:
            fav_side, fav_price, fav_fair = 'a', current_a, fair_a
            dog_side, dog_price, dog_fair = 'b', current_b, fair_b
        else:
            fav_side, fav_price, fav_fair = 'b', current_b, fair_b
            dog_side, dog_price, dog_fair = 'a', current_a, fair_a

        if fav_price == fav_fair:
            # sin edge, sin plan
            return None

        limit_price = max(0.0, min(1.0, round(fav_price - avg_volatility, 4))) \
            if fav_price > fav_fair else \
            max(0.0, min(1.0, round(dog_price - avg_volatility, 4)))

        category = market.get('type')

        if fav_price > fav_fair:
            # Caso 1: favorita sobrevalorada -> se espera que caiga
            case = 'favorite_overvalued'
            market_leg = {'side': dog_side, 'action': 'market', 'price': dog_price}
            limit_leg = {'side': fav_side, 'action': 'limit', 'price': limit_price}
        else:
            # Caso 2: favorita infravalorada -> se espera que suba
            case = 'favorite_undervalued'
            market_leg = {'side': fav_side, 'action': 'market', 'price': fav_price}
            limit_leg = {'side': dog_side, 'action': 'limit', 'price': limit_price}

        # Fees reales de Kalshi sobre ambas patas. La pata límite se cobra
        # al mismo nivel que una taker (fail-closed: no hay cifra confiable
        # de descuento maker por serie, ver APIs/kalshi_fees.py) usando su
        # precio de ejecución esperado (limit_price), no el precio actual.
        market_fee = order_fee(market_leg['price'], category=category)
        limit_fee = order_fee(limit_leg['price'], category=category)
        if market_fee is None or limit_fee is None:
            return None

        raw_edge = abs(fav_price - fav_fair)
        net_return_opportunity = round(max(0.0, raw_edge - market_fee - limit_fee), 4)

        return {
            'case': case,
            'market_leg': market_leg,
            'limit_leg': limit_leg,
            'return_opportunity': net_return_opportunity,
        }

    def execute_order_placement(self):
        """NO ejecuta órdenes reales todavía — Kairos no tiene wiring de
        auth/firma para colocación de órdenes (línea roja del proyecto,
        requiere autorización explícita del usuario paso a paso). Por ahora
        esto es un worker de solo lectura/propuesta: reporta las
        oportunidades encontradas (incluyendo el plan de las dos patas)."""
        return self.opportunities
