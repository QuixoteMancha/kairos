"""Cliente websocket real de Kalshi -- 2026-08-02, pedido explícito del
usuario: "que el scan vaya guardando las oportunidades market arb
detectadas, mediante websocket".

Conexión: `wss://api.kalshi.com/trade-api/ws/v2` (investigado y
verificado 2026-08-02) -- requiere autenticación RSA-PSS incluso para
canales públicos (`outer_apis/kalshi/auth.py`), a diferencia del REST de
lectura ya usado en todo el resto de Kairos.

Canal usado: `ticker` (precio en tiempo real por mercado) -- suficiente
para detectar MarketArb (same-ticket/cross-ticket), no hace falta
`orderbook_delta` (profundidad completa del book) para esta estrategia
puntual.

SOLO LECTURA -- esto es un canal de DATOS, no de órdenes. La línea roja
del proyecto (sin ejecución real) sigue vigente, este módulo no la toca."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import websockets

from outer_apis.kalshi.auth import sign_request

_WS_URL = "wss://api.kalshi.com/trade-api/ws/v2"
_WS_PATH = "/trade-api/ws/v2"
_MAX_TICKERS_PER_SUBSCRIBE = 500  # margen defensivo, Kalshi no publica un límite exacto documentado


class KalshiWebSocketError(Exception):
    pass


def _build_auth_headers() -> dict[str, str]:
    """Fail-closed: lanza `KalshiWebSocketError` si no hay credenciales
    reales -- nunca intenta conectar sin auth real (Kalshi lo
    rechazaría igual, pero mejor fallar temprano con un mensaje claro)."""
    headers = sign_request("GET", _WS_PATH)
    if headers is None:
        raise KalshiWebSocketError(
            "Sin credenciales reales de Kalshi (/data/secrets/kalshi_key.pem) "
            "-- no se puede autenticar el websocket."
        )
    return headers


async def stream_ticker_updates(tickers: list[str]) -> AsyncIterator[dict[str, Any]]:
    """Generador async: yields un dict por cada actualización real de
    precio (`{"ticker": str, "price": float, "yes_bid": float|None,
    "yes_ask": float|None, "ts": ...}` -- shape crudo del mensaje de
    Kalshi, sin normalizar acá) para los `tickers` pedidos.

    Reconecta automáticamente si la conexión se cae (heartbeat/ping-pong
    real de Kalshi, ver docstring del módulo) -- loop infinito hasta que
    el caller cancele la tarea.

    Fail-closed: si no hay tickers reales o no hay credenciales, no
    intenta conectar."""
    if not tickers:
        return

    headers = _build_auth_headers()

    while True:
        try:
            async with websockets.connect(_WS_URL, additional_headers=headers) as ws:
                subscribe_msg = {
                    "id": 1, "cmd": "subscribe",
                    "params": {"channels": ["ticker"], "market_tickers": tickers[:_MAX_TICKERS_PER_SUBSCRIBE]},
                }
                await ws.send(json.dumps(subscribe_msg))

                async for raw_message in ws:
                    try:
                        message = json.loads(raw_message)
                    except json.JSONDecodeError:
                        continue
                    if message.get("type") == "ticker":
                        yield message.get("msg", {})
        except (websockets.ConnectionClosed, OSError):
            # Reconexión real (ver docstring: heartbeat cada 10s, la
            # conexión puede caer por red) -- headers se re-firman con
            # timestamp fresco en cada reintento (la firma expira).
            headers = _build_auth_headers()
            await asyncio.sleep(2)
            continue
