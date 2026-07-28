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
#       - LIMIT order en la favorita, al precio actual menos el ajuste
#         de precio calculado (ver más abajo).
#
#   Caso 2 — favorita INFRAVALORADA (fav_price < fav_fair):
#     se espera que la favorita SUBA hacia su fair price, y la
#     no-favorita CAIGA. Entonces:
#       - MARKET order en la favorita, ahora, mientras está barata.
#       - LIMIT order en la no-favorita, al precio actual menos el ajuste
#         de precio calculado (ver más abajo).
#
# GUARDRAIL DE PLAUSIBILIDAD (2026-07-20, ver memoria
# kairos-limitarb-plausibility-guardrail): el `return_opportunity` (profit)
# YA NO se calcula desde el edge crudo del fair-price model
# (|fav_price - fav_fair|), que resultó no tener techo — un modelo de
# fair-price con información incompleta (ej. tenis sin datos de
# lesión/forma) puede reportar un edge de 40 puntos porcentuales que casi
# seguro es error de modelo, no una oportunidad real. En cambio, el ajuste
# de precio de la pata límite (`price_adjustment`, cuánto se aleja del
# precio actual) se calcula con una regresión lineal:
#
#     price_adjustment = W_EDGE * raw_edge + W_VOLATILITY * (avg_volatility / 2)
#
# - El término de volatilidad usa la MITAD de avg_volatility (no el 100%):
#   avg_volatility es un promedio esperado de la simulación Monte Carlo,
#   no una garantía para este partido puntual — usar la mitad es el ajuste
#   conservador sustentado en datos que evita sobreconfiar en el promedio.
# - raw_edge sigue pesando (40% inicial) porque es señal real del modelo
#   de fair-price, pero ya NO determina el profit por sí solo sin límite.
# - W_EDGE=0.4 / W_VOLATILITY=0.6 son pesos INICIALES, no calibrados
#   todavía con datos reales — están pensados para recalibrarse más
#   adelante contra resultados reales observados (requiere loggear cada
#   señal + el movimiento de precio real posterior, ver
#   database/limit_arb_signals.py). Hasta que exista ese loop de
#   entrenamiento, son un punto de partida razonado, no una cifra final.
# - `return_opportunity` (el profit reportado) se deriva del costo real de
#   armar ambas patas al `price_adjustment` calculado, no del edge crudo
#   — esto lo acota estructuralmente en vez de necesitar un chequeo
#   posterior "si supera tal techo, rechazar" (que nunca se activaría
#   correctamente si el número ya viene sin relación con la volatilidad).

from outer_apis.kalshi.client import KalshiAPI
from outer_apis.kalshi.fees import order_fee
from models.basemodel import Model
from database.accounting.writer import log_signal
from strategies.paper_trading import open_paper_trade

MIN_VOLATILITY = 0.10
MIN_RETURN_OPPORTUNITY = 0.02

# Pesos iniciales de la regresión lineal del ajuste de precio de la pata
# límite. Ver nota de diseño arriba — no calibrados todavía con datos
# reales, son el punto de partida acordado.
W_EDGE = 0.4
W_VOLATILITY = 0.6


class LimitArb:
    def __init__(self, api: KalshiAPI, markets):
        self.api = api
        self.markets = markets
        self.model = Model()
        self.opportunities = []

    @staticmethod
    def _real_ticker_for_side(market, side):
        """Devuelve el ticker REAL de Kalshi fetcheable para ese lado.
        Cross-ticket (tenis/team sports, ver kalshi_tennis_markets.py /
        kalshi_team_sport_markets.py): el market combinado trae
        ticker_a/ticker_b (tickers reales de cada pierna) porque market['id']
        ahí es un event_ticker sintético que nunca es fetcheable. Single-
        ticket (crypto/clima/economía/financieros): no hay ticker_a/
        ticker_b, el ticker real ES market['id']."""
        ticker_a = market.get('ticker_a')
        ticker_b = market.get('ticker_b')
        if ticker_a and ticker_b:
            return ticker_a if side == 'a' else ticker_b
        return market.get('id')

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
                opportunity = {
                    'market_id': market['id'],
                    'avg_volatility': avg_volatility,
                    'return_opportunity': plan['return_opportunity'],
                    'case': plan['case'],
                    'market_leg': plan['market_leg'],
                    'limit_leg': plan['limit_leg'],
                    'price_adjustment': plan['price_adjustment'],
                    'raw_edge': plan['raw_edge'],
                }
                self.opportunities.append(opportunity)
                # Loggeado para poder entrenar W_EDGE/W_VOLATILITY contra
                # resultados reales más adelante (ver database/limit_arb_signals.py).
                # Fail-closed: un fallo de log nunca debe tumbar el scan.
                try:
                    signal_id = log_signal(market_id=market['id'], avg_volatility=avg_volatility,
                               raw_edge=plan['raw_edge'], price_adjustment=plan['price_adjustment'],
                               w_edge=W_EDGE, w_volatility=W_VOLATILITY,
                               case=plan['case'], return_opportunity=plan['return_opportunity'],
                               fav_side=plan['fav_side'], fav_price_at_signal=plan['fav_price_at_signal'],
                               limit_leg_side=plan['limit_leg']['side'],
                               limit_leg_price=plan['limit_leg']['price'],
                               market_close_time=market.get('close_time'),
                               market_leg_side=plan['market_leg']['side'],
                               market_leg_price_at_signal=plan['market_leg']['price'],
                               market_leg_ticker=self._real_ticker_for_side(market, plan['market_leg']['side']),
                               limit_leg_ticker=self._real_ticker_for_side(market, plan['limit_leg']['side']))
                    # Simulated paper position (100% fake money, see
                    # strategies/paper_trading.py) sized via fractional
                    # Kelly against this signal -- part of the same
                    # fail-closed try/except so a paper-trading failure
                    # never blocks the real (read-only/proposal) scan.
                    open_paper_trade(signal_id=signal_id, market_id=market['id'], case=plan['case'],
                                      avg_volatility=avg_volatility, raw_edge=plan['raw_edge'],
                                      return_opportunity=plan['return_opportunity'],
                                      price_adjustment=plan['price_adjustment'],
                                      market_leg_price=plan['market_leg']['price'],
                                      category=market.get('type'))
                except Exception:
                    pass

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

        raw_edge = abs(fav_price - fav_fair)

        # Ajuste de precio vía regresión lineal (ver nota de diseño al
        # inicio del archivo). Acota estructuralmente el profit reportado
        # más abajo, en vez de depender de un techo chequeado a posteriori.
        price_adjustment = round(W_EDGE * raw_edge + W_VOLATILITY * (avg_volatility / 2.0), 4)

        limit_price = max(0.0, min(1.0, round(fav_price - price_adjustment, 4))) \
            if fav_price > fav_fair else \
            max(0.0, min(1.0, round(dog_price - price_adjustment, 4)))

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
        # de descuento maker por serie, ver outer_apis/kalshi_fees.py) usando su
        # precio de ejecución esperado (limit_price), no el precio actual.
        market_fee = order_fee(market_leg['price'], category=category)
        limit_fee = order_fee(limit_leg['price'], category=category)
        if market_fee is None or limit_fee is None:
            return None

        # Profit real si ambas patas llegan a llenarse a estos precios:
        # costo total de armar las dos patas complementarias vs el payout
        # garantizado de $1. Acotado estructuralmente por price_adjustment
        # (no puede superarlo salvo por redondeo/fees), no por un chequeo
        # posterior contra un techo separado.
        total_cost = market_leg['price'] + limit_leg['price'] + market_fee + limit_fee
        net_return_opportunity = round(max(0.0, 1.0 - total_cost), 4)

        return {
            'case': case,
            'market_leg': market_leg,
            'limit_leg': limit_leg,
            'return_opportunity': net_return_opportunity,
            'price_adjustment': price_adjustment,
            'raw_edge': raw_edge,
            'fav_side': fav_side,
            'fav_price_at_signal': fav_price,
        }

    def execute_order_placement(self):
        """NO ejecuta órdenes reales todavía — Kairos no tiene wiring de
        auth/firma para colocación de órdenes (línea roja del proyecto,
        requiere autorización explícita del usuario paso a paso). Por ahora
        esto es un worker de solo lectura/propuesta: reporta las
        oportunidades encontradas (incluyendo el plan de las dos patas)."""
        return self.opportunities
