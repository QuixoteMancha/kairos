"""Tests del cliente websocket de Kalshi (2026-08-02, pedido explícito
del usuario). Mockea toda conexión de red real -- nunca abre un
websocket real contra Kalshi en los tests.

Sin pytest-asyncio (no es dependencia del proyecto) -- cada test corre
su propia coroutine vía `asyncio.run()`."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import websockets

from outer_apis.kalshi.websocket_client import stream_ticker_updates, KalshiWebSocketError


async def _collect(gen, limit):
    out = []
    async for item in gen:
        out.append(item)
        if len(out) >= limit:
            break
    return out


class _FakeWebSocket:
    """Simula una conexión websocket real: async context manager +
    async iterable de mensajes."""

    def __init__(self, messages, raise_after=None):
        self._messages = messages
        self._raise_after = raise_after
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def send(self, data):
        self.sent.append(data)

    def __aiter__(self):
        return self._message_gen()

    async def _message_gen(self):
        for m in self._messages:
            yield m
        if self._raise_after:
            raise self._raise_after


def test_no_connection_attempted_without_tickers():
    async def run():
        with patch("outer_apis.kalshi.websocket_client.websockets.connect") as mock_connect:
            result = [item async for item in stream_ticker_updates([])]
        return result, mock_connect

    result, mock_connect = asyncio.run(run())
    assert result == []
    mock_connect.assert_not_called()


def test_raises_without_real_credentials():
    async def run():
        with patch("outer_apis.kalshi.websocket_client.sign_request", return_value=None):
            async for _ in stream_ticker_updates(["KXBTC15M-TEST"]):
                pass

    try:
        asyncio.run(run())
        assert False, "esperaba KalshiWebSocketError"
    except KalshiWebSocketError:
        pass


def test_yields_only_ticker_type_messages():
    fake_ws = _FakeWebSocket(messages=[
        json.dumps({"type": "ticker", "msg": {"ticker": "KXBTC15M-TEST", "price": 0.55}}),
        json.dumps({"type": "subscribed", "msg": {}}),  # no es ticker, se descarta
        json.dumps({"type": "ticker", "msg": {"ticker": "KXBTC15M-TEST", "price": 0.56}}),
    ])

    async def run():
        with patch("outer_apis.kalshi.websocket_client.sign_request", return_value={"KALSHI-ACCESS-KEY": "x"}), \
             patch("outer_apis.kalshi.websocket_client.websockets.connect", return_value=fake_ws):
            return await _collect(stream_ticker_updates(["KXBTC15M-TEST"]), limit=2)

    result = asyncio.run(run())
    assert len(result) == 2
    assert result[0]["price"] == 0.55
    assert result[1]["price"] == 0.56


def test_skips_malformed_json_without_crashing():
    fake_ws = _FakeWebSocket(messages=[
        "not valid json {{{",
        json.dumps({"type": "ticker", "msg": {"ticker": "X", "price": 0.5}}),
    ])

    async def run():
        with patch("outer_apis.kalshi.websocket_client.sign_request", return_value={"KALSHI-ACCESS-KEY": "x"}), \
             patch("outer_apis.kalshi.websocket_client.websockets.connect", return_value=fake_ws):
            return await _collect(stream_ticker_updates(["X"]), limit=1)

    result = asyncio.run(run())
    assert result == [{"ticker": "X", "price": 0.5}]


def test_sends_subscribe_message_with_requested_tickers():
    fake_ws = _FakeWebSocket(messages=[
        json.dumps({"type": "ticker", "msg": {"ticker": "A", "price": 0.5}}),
    ])

    async def run():
        with patch("outer_apis.kalshi.websocket_client.sign_request", return_value={"KALSHI-ACCESS-KEY": "x"}), \
             patch("outer_apis.kalshi.websocket_client.websockets.connect", return_value=fake_ws):
            await _collect(stream_ticker_updates(["A", "B"]), limit=1)
        return fake_ws

    fake_ws = asyncio.run(run())
    sent = json.loads(fake_ws.sent[0])
    assert sent["cmd"] == "subscribe"
    assert sent["params"]["channels"] == ["ticker"]
    assert sent["params"]["market_tickers"] == ["A", "B"]


def test_reconnects_after_connection_closed():
    call_count = {"n": 0}

    def _connect_side_effect(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _FakeWebSocket(
                messages=[json.dumps({"type": "ticker", "msg": {"ticker": "A", "price": 0.1}})],
                raise_after=websockets.ConnectionClosed(None, None),
            )
        return _FakeWebSocket(
            messages=[json.dumps({"type": "ticker", "msg": {"ticker": "A", "price": 0.2}})],
        )

    async def run():
        with patch("outer_apis.kalshi.websocket_client.sign_request", return_value={"KALSHI-ACCESS-KEY": "x"}), \
             patch("outer_apis.kalshi.websocket_client.websockets.connect", side_effect=_connect_side_effect), \
             patch("outer_apis.kalshi.websocket_client.asyncio.sleep", new=AsyncMock()):
            return await _collect(stream_ticker_updates(["A"]), limit=2)

    result = asyncio.run(run())
    assert call_count["n"] == 2  # se reconectó una vez
    assert [r["price"] for r in result] == [0.1, 0.2]
