"""PRUEBA DE CAPITAL REAL, autorizada explícitamente por el usuario
(2026-07-30) -- primera vez que Kairos coloca una orden real contra Kalshi.
Alcance deliberadamente acotado a UN partido de tenis, UNA sola corrida.

Qué hace:
  1. Fetchea mercados de tenis en vivo (mismo camino que engine.py), corre
     LimitArb.evaluate_risk_and_return_opportunities() -- el mismo motor de
     dwell-time que ya corre en producción en modo solo-lectura.
  2. Filtra candidatos: case in {favorite_overvalued, favorite_undervalued},
     price_adjustment dentro de una banda alrededor del 4% pedido
     (TARGET_ADJUSTMENT ± TOLERANCE) para no forzar una señal que el modelo
     no generó -- fail-closed: si ninguna señal cae en la banda, NO se
     inventa una eligiendo "la más parecida" fuera de rango.
  3. Con el balance REAL de Kalshi (leído en vivo, no hardcodeado), calcula
     el tamaño de la prueba = CAPITAL_PCT del balance, repartido entre
     ambas patas proporcional a su precio.
  4. DRY-RUN por defecto: imprime el plan completo (ticker, side, count,
     price, TIF) sin llamar a ningún endpoint de escritura. Solo con
     --live Y tipeando "SI" en el prompt de confirmación coloca las 2
     órdenes reales (market leg IOC, limit leg GTC).

Uso:
    python scripts/live_limit_arb_tennis_test.py            # dry-run
    python scripts/live_limit_arb_tennis_test.py --live      # coloca órdenes reales

Guardrails duros (no se pueden saltear con flags):
  - CAPITAL_PCT = 0.05 (5% del balance real, tal como autorizó el usuario).
  - MAX_TOTAL_USD: tope absoluto en dólares como segunda red de seguridad
    independiente del %, por si el balance leído fuera erróneamente alto.
  - Solo tenis, solo 1 mercado, solo 1 corrida (no hay loop).
  - Si el balance real es menor a MIN_BALANCE_USD, aborta (fail-closed: no
    tiene sentido "probar" con céntimos, y protege contra leer un balance
    ya castigado por otra operación concurrente)."""

from __future__ import annotations

import argparse
import sys

from outer_apis.kalshi.client import KalshiAPI
from outer_apis.kalshi.markets.tennis import build_tennis_markets
from outer_apis.sports.tennis.match_history import prefetch_player_histories
from outer_apis.kalshi.orders import KalshiOrdersAPI, KalshiOrderError
from strategies.limit_arb import LimitArb, MIN_VOLATILITY, MIN_RETURN_OPPORTUNITY

TENNIS_SERIES = ("KXATPMATCH", "KXWTAMATCH")

CAPITAL_PCT = 0.05          # 5% del balance real -- autorizado explícitamente 2026-07-30
# El 4% original pedido por el usuario resultó estructuralmente casi
# inalcanzable: price_adjustment = 0.4*raw_edge + 0.6*(avg_volatility/2), y
# solo el término de volatilidad ya pone un piso de ~6-6.8% con la
# avg_volatility que el modelo Markov de tenis está calculando hoy
# (clusterizada ~0.20-0.23 en ATP/WTA singles, ver memoria de la sesión
# 2026-07-30). Usuario aceptó explícitamente adaptar la banda a la
# volatilidad real del modelo en vez de forzar 4% exacto.
TARGET_ADJUSTMENT = 0.065   # centro de la banda real observada del modelo
ADJUSTMENT_TOLERANCE = 0.025  # banda [4%, 9%]
MAX_TOTAL_USD = 15.00       # tope absoluto duro, segunda red de seguridad
MIN_BALANCE_USD = 20.00     # bajo esto, abortar -- no tiene sentido "probar"
MIN_CONTRACTS = 1           # Kalshi exige contratos enteros mínimo 1 en la práctica


def _select_candidate(opportunities: list[dict]) -> dict | None:
    """Fail-closed: banda alrededor de TARGET_ADJUSTMENT, entre los que ya
    pasaron los umbrales de LimitArb (MIN_VOLATILITY/MIN_RETURN_OPPORTUNITY,
    aplicados por evaluate_risk_and_return_opportunities antes de llegar
    acá). Si hay más de uno en la banda, el de mayor return_opportunity."""
    band = [
        opp for opp in opportunities
        if abs(opp['price_adjustment'] - TARGET_ADJUSTMENT) <= ADJUSTMENT_TOLERANCE
    ]
    if not band:
        return None
    return max(band, key=lambda o: o['return_opportunity'])


def _real_ticker(market: dict, side: str) -> str | None:
    ticker_a = market.get('ticker_a')
    ticker_b = market.get('ticker_b')
    if ticker_a and ticker_b:
        return ticker_a if side == 'a' else ticker_b
    return market.get('id')


def _kalshi_side_for_leg(leg: dict, price: float) -> tuple[str, float]:
    """Traduce {'side': 'a'|'b', 'price': fair_a_o_b} al par (side_kalshi,
    price_yes) que espera Kalshi -- side='bid' comprando el ticker YES de
    ESE leg directamente a su propio precio (cada leg de tenis en Kalshi
    ya es su propio ticker YES/NO independiente, ver build_tennis_markets),
    nunca 'ask' porque estamos comprando, no vendiendo."""
    return "bid", price


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true",
                         help="Coloca órdenes reales (default: dry-run, no toca la API de escritura)")
    args = parser.parse_args()

    print("== Prueba de capital real: dwell-time limit-arb, 1 partido de tenis ==")
    print(f"Modo: {'LIVE (dinero real)' if args.live else 'DRY-RUN (solo imprime el plan)'}")

    orders_api = KalshiOrdersAPI().initialize()

    # 1) Balance real -- SIEMPRE leído en vivo, nunca asumido.
    balance_data = orders_api.get_portfolio_balance()
    balance_usd = float(balance_data["balance_dollars"])
    print(f"Balance real Kalshi: ${balance_usd:.2f}")

    if balance_usd < MIN_BALANCE_USD:
        print(f"ABORTA: balance (${balance_usd:.2f}) por debajo de MIN_BALANCE_USD (${MIN_BALANCE_USD:.2f}).")
        sys.exit(1)

    total_budget_usd = min(balance_usd * CAPITAL_PCT, MAX_TOTAL_USD)
    print(f"Presupuesto de la prueba (5% del balance, tope ${MAX_TOTAL_USD:.2f}): ${total_budget_usd:.2f}")

    # 2) Mercados de tenis en vivo, mismo camino que engine.py.
    kalshi_api = KalshiAPI()
    kalshi_api.initialize()

    tennis_legs = []
    for series in TENNIS_SERIES:
        tennis_legs.extend(kalshi_api.get_markets(series_ticker=series))
    tennis_markets = build_tennis_markets(tennis_legs)

    if not tennis_markets:
        print("ABORTA: no hay mercados de tenis disponibles ahora mismo.")
        sys.exit(1)

    player_names = [
        name for m in tennis_markets
        for name in (m.get('player_a_name'), m.get('player_b_name')) if name
    ]
    prefetch_player_histories(player_names)

    # 3) Correr el motor real de dwell-time (mismo código de producción).
    larb = LimitArb(api=kalshi_api, markets=tennis_markets)
    opportunities = larb.evaluate_risk_and_return_opportunities()
    print(f"Oportunidades encontradas (umbral vol>={MIN_VOLATILITY}, ret>={MIN_RETURN_OPPORTUNITY}): {len(opportunities)}")

    candidate = _select_candidate(opportunities)
    if candidate is None:
        print(f"ABORTA: ninguna oportunidad con price_adjustment en "
              f"[{TARGET_ADJUSTMENT - ADJUSTMENT_TOLERANCE:.2%}, {TARGET_ADJUSTMENT + ADJUSTMENT_TOLERANCE:.2%}] ahora mismo. "
              f"No se fuerza una señal fuera de la banda pedida.")
        sys.exit(1)

    market = next(m for m in tennis_markets if m['id'] == candidate['market_id'])
    market_leg = candidate['market_leg']
    limit_leg = candidate['limit_leg']

    market_ticker = _real_ticker(market, market_leg['side'])
    limit_ticker = _real_ticker(market, limit_leg['side'])

    if not market_ticker or not limit_ticker:
        print("ABORTA: no se pudo resolver el ticker real de Kalshi para una de las patas.")
        sys.exit(1)

    # 4) Sizing: presupuesto repartido proporcional al precio de cada pata,
    #    contratos enteros (Kalshi no acepta fracciones de contrato en la
    #    práctica pese a que la API acepta 2 decimales).
    market_price = market_leg['price']
    limit_price = limit_leg['price']
    half_budget = total_budget_usd / 2.0
    market_count = max(MIN_CONTRACTS, int(half_budget / market_price)) if market_price > 0 else 0
    limit_count = max(MIN_CONTRACTS, int(half_budget / limit_price)) if limit_price > 0 else 0

    if market_count < MIN_CONTRACTS or limit_count < MIN_CONTRACTS:
        print("ABORTA: presupuesto insuficiente para al menos 1 contrato por pata a estos precios.")
        sys.exit(1)

    print("\n--- Plan de la prueba ---")
    print(f"Partido (market_id): {market['id']}  ({market.get('player_a_name')} vs {market.get('player_b_name')})")
    print(f"Caso: {candidate['case']}  |  avg_volatility={candidate['avg_volatility']:.4f}  |  "
          f"price_adjustment={candidate['price_adjustment']:.4f}  |  return_opportunity={candidate['return_opportunity']:.4f}")
    print(f"Pata MERCADO (IOC): ticker={market_ticker}  side=bid  price=${market_price:.4f}  count={market_count}  "
          f"costo=${market_price * market_count:.2f}")
    print(f"Pata LÍMITE (GTC):  ticker={limit_ticker}  side=bid  price=${limit_price:.4f}  count={limit_count}  "
          f"costo si llena=${limit_price * limit_count:.2f}")
    print(f"Costo total si ambas patas llenan: ${market_price * market_count + limit_price * limit_count:.2f} "
          f"de un presupuesto de ${total_budget_usd:.2f}")

    if not args.live:
        print("\nDRY-RUN: no se colocó ninguna orden real. Corré con --live para ejecutar.")
        return

    print("\n*** ESTO VA A COLOCAR ÓRDENES REALES CON DINERO REAL ***")
    confirm = input("Tipeá exactamente SI para confirmar: ")
    if confirm.strip() != "SI":
        print("Cancelado por el usuario (confirmación no coincide).")
        sys.exit(1)

    try:
        market_side, market_yes_price = _kalshi_side_for_leg(market_leg, market_price)
        resp_market = orders_api.place_order(
            ticker=market_ticker,
            side=market_side,
            count=f"{market_count:.2f}",
            price=f"{market_yes_price:.4f}",
            time_in_force="immediate_or_cancel",
        )
        print(f"Pata MERCADO colocada: order_id={resp_market.get('order_id')} "
              f"fill_count={resp_market.get('fill_count')} avg_fill_price={resp_market.get('average_fill_price')}")
    except KalshiOrderError as exc:
        print(f"FALLO colocando la pata mercado, ABORTANDO sin tocar la pata límite: {exc}")
        sys.exit(1)

    try:
        limit_side, limit_yes_price = _kalshi_side_for_leg(limit_leg, limit_price)
        resp_limit = orders_api.place_order(
            ticker=limit_ticker,
            side=limit_side,
            count=f"{limit_count:.2f}",
            price=f"{limit_yes_price:.4f}",
            time_in_force="good_till_canceled",
        )
        print(f"Pata LÍMITE colocada: order_id={resp_limit.get('order_id')}")
    except KalshiOrderError as exc:
        print(f"FALLO colocando la pata límite (la pata mercado YA SE EJECUTÓ, quedaste con exposición "
              f"solo en un lado -- revisar manualmente en Kalshi): {exc}")
        sys.exit(1)

    print("\nPrueba colocada. Verificar manualmente en Kalshi y vía GET /api/portfolio/kalshi.")


if __name__ == "__main__":
    main()
