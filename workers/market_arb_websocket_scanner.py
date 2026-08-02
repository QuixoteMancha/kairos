"""Scanner de MarketArb en tiempo real vía websocket -- 2026-08-02,
pedido explícito del usuario: "que el scan vaya guardando las
oportunidades market arb detectadas, mediante websocket en base, luego
en los próximos días haremos una revisión. El scan se debe hacer en
todos los mercados binarios, pero de momento sólo será deportes + clima
+ crypto".

DISEÑO HÍBRIDO (deliberado, no un descuido): el mensaje real del canal
`ticker` del websocket de Kalshi no tiene un schema de campos
confirmado en documentación pública -- en vez de adivinar nombres de
campo (`yes_ask`/`yes_bid`/etc.) y arriesgar parsear mal un precio real,
el websocket se usa SOLO como disparador de "este ticker cambió,
revisalo ahora" -- el precio real que se evalúa siempre viene del REST
ya probado (`KalshiAPI.get_markets_by_tickers`, campos
`yes_ask_dollars`/`no_ask_dollars` confirmados correctos, ver
`outer_apis/kalshi/client.py::_normalize_market`). Esto es más lento
que confiar ciegamente en el payload del websocket, pero es seguro --
se puede optimizar más adelante una vez confirmado en vivo el schema
real de Kalshi.

ALCANCE (pedido explícito, NO todos los mercados binarios todavía):
tenis + fútbol + team-sports (ya construidos, mismos builders que
`engine.py`) + clima (KXHIGH{ciudad}) + crypto 15min. Cross-ticket arb
para tenis/team-sports (pares reales), same-ticket para el resto.

SOLO LECTURA/REGISTRO -- ninguna orden se coloca, línea roja del
proyecto sigue vigente."""

from __future__ import annotations

import asyncio
from typing import Any

from database.db import init_db
from database.accounting.marketarbwebsocketsignal import MarketArbWebsocketSignal
from outer_apis.kalshi.client import KalshiAPI
from outer_apis.kalshi.markets.tennis import build_tennis_markets
from outer_apis.kalshi.markets.football import build_football_markets
from outer_apis.kalshi.markets.team_sport import build_team_sport_markets
from outer_apis.kalshi.websocket_client import stream_ticker_updates
from strategies.market_arb import MarketArb

_TENNIS_SERIES = ("KXATPMATCH", "KXWTAMATCH")
_FOOTBALL_SERIES = ("KXMLSGAME", "KXUEFAGAME")
_TEAM_SPORT_SERIES = ("KXMLBGAME", "KXNBAGAME", "KXNHLGAME", "KXNFLGAME")
_CRYPTO_15M_SERIES = ("KXBTC15M", "KXETH15M", "KXSOL15M")
# Ciudades reales con mercado de clima activo en Kalshi (verificado en
# vivo 2026-08-02) -- lista chica a propósito, se puede ampliar después.
_WEATHER_SERIES = ("KXHIGHNY", "KXHIGHCHI", "KXHIGHAUS", "KXHIGHMIA", "KXHIGHDEN", "KXHIGHPHIL")

_CATEGORY_BY_SERIES_PREFIX = {
    "KXATPMATCH": "tennis", "KXWTAMATCH": "tennis",
    "KXMLSGAME": "football", "KXUEFAGAME": "football",
    "KXMLBGAME": "team_sport", "KXNBAGAME": "team_sport",
    "KXNHLGAME": "team_sport", "KXNFLGAME": "team_sport",
    "KXBTC15M": "crypto", "KXETH15M": "crypto", "KXSOL15M": "crypto",
    "KXHIGHNY": "weather", "KXHIGHCHI": "weather", "KXHIGHAUS": "weather",
    "KXHIGHMIA": "weather", "KXHIGHDEN": "weather", "KXHIGHPHIL": "weather",
}


def _category_for_ticker(ticker: str) -> str:
    for prefix, category in _CATEGORY_BY_SERIES_PREFIX.items():
        if ticker.startswith(prefix):
            return category
    return "unknown"


def build_market_registry(api: KalshiAPI) -> dict[str, dict[str, Any]]:
    """Construye `{ticker: market_dict}` -- CADA ticker de cada mercado
    (ambas patas si es cross-ticket) apunta al MISMO `market_dict`, para
    poder re-evaluar el mercado completo apenas cambia cualquiera de sus
    2 patas. Fail-closed por serie: una serie sin mercados abiertos
    simplemente no aporta nada, no rompe el resto."""
    registry: dict[str, dict[str, Any]] = {}

    tennis_legs = []
    for series in _TENNIS_SERIES:
        tennis_legs.extend(api.get_markets(series_ticker=series))
    for market in build_tennis_markets(tennis_legs):
        market["category"] = "tennis"
        registry[market["ticker_a"]] = market
        registry[market["ticker_b"]] = market

    football_legs = []
    for series in _FOOTBALL_SERIES:
        football_legs.extend(api.get_markets(series_ticker=series))
    for market in build_football_markets(football_legs):
        market["category"] = "football"
        registry[market.get("ticker_a", market["id"])] = market
        registry[market.get("ticker_b", market["id"])] = market

    for series in _TEAM_SPORT_SERIES:
        legs = api.get_markets(series_ticker=series)
        for market in build_team_sport_markets(legs, series):
            market["category"] = "team_sport"
            registry[market["ticker_a"]] = market
            registry[market["ticker_b"]] = market

    for series in _CRYPTO_15M_SERIES:
        for market in api.get_markets(series_ticker=series):
            market["category"] = "crypto"
            market["ticker_a"] = market["ticker_b"] = market["id"]
            registry[market["id"]] = market

    for series in _WEATHER_SERIES:
        for market in api.get_markets(series_ticker=series):
            market["category"] = "weather"
            market["ticker_a"] = market["ticker_b"] = market["id"]
            registry[market["id"]] = market

    return registry


def _refresh_and_check(api: KalshiAPI, market: dict[str, Any]) -> None:
    """Re-lee AMBAS patas del mercado por REST (fuente de verdad real,
    ver docstring del módulo), actualiza `current_a_price`/
    `current_b_price` in-place, y guarda una fila si hay arb real."""
    ticker_a, ticker_b = market["ticker_a"], market["ticker_b"]
    fresh = api.get_markets_by_tickers(list({ticker_a, ticker_b}))

    leg_a = fresh.get(ticker_a)
    leg_b = fresh.get(ticker_b)
    if leg_a is None or leg_b is None:
        return  # mercado purgado/gone -- fail-closed, no se evalúa con datos viejos

    market["current_a_price"] = leg_a.get("current_a_price")
    market["current_b_price"] = leg_b.get("current_a_price") if ticker_a != ticker_b else leg_a.get("current_b_price")

    marb = MarketArb(api=api, markets=[market])
    opportunities = marb.evaluate_risk_and_return_opportunities()
    if not opportunities:
        return

    init_db()
    for opp in opportunities:
        MarketArbWebsocketSignal.create(
            market_id=opp["market_id"], category=market.get("category", "unknown"),
            ticker_a=ticker_a, ticker_b=ticker_b,
            price_a=market["current_a_price"], price_b=market["current_b_price"],
            return_opportunity=opp["return_opportunity"],
        )
        print(f"[market_arb_ws] OPORTUNIDAD REAL guardada: {opp}")


async def run() -> None:
    api = KalshiAPI()
    api.initialize()

    registry = build_market_registry(api)
    tickers = list(registry.keys())
    print(f"[market_arb_ws] escuchando {len(tickers)} tickers reales ({len(set(id(m) for m in registry.values()))} mercados únicos)")

    async for update in stream_ticker_updates(tickers):
        ticker = update.get("ticker") or update.get("market_ticker")
        if not ticker or ticker not in registry:
            continue
        try:
            _refresh_and_check(api, registry[ticker])
        except Exception as e:
            print(f"[market_arb_ws] error procesando {ticker}: {e}")


if __name__ == "__main__":
    asyncio.run(run())
