"""Tests for KalshiClient.get_order_book() — real endpoint shape confirmed
live 2026-07-05 against external-api.kalshi.com/trade-api/v2/markets/{ticker}/orderbook."""

from unittest.mock import MagicMock

from src.scrapers.kalshi_client import KalshiClient

REAL_SHAPE_RESPONSE = {
    "orderbook_fp": {
        "no_dollars": [["0.0100", "230340.00"], ["0.0200", "5500.00"]],
        "yes_dollars": [["0.0100", "372.00"], ["0.0200", "12.99"]],
    }
}


def _client_with_get(payload, status_ok=True, raise_exc=None):
    client = KalshiClient()
    mock_resp = MagicMock()
    if raise_exc:
        client._get = MagicMock(side_effect=raise_exc)
        return client
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value=payload)
    client._get = MagicMock(return_value=mock_resp)
    return client


def test_get_order_book_normalizes_real_shape():
    client = _client_with_get(REAL_SHAPE_RESPONSE)
    book = client.get_order_book("KXFED-24DEC-T4.50")

    assert set(book.keys()) == {"yes", "no"}
    assert book["yes"] == [(0.02, 12.99), (0.01, 372.0)]
    assert book["no"] == [(0.02, 5500.0), (0.01, 230340.0)]
    for side in ("yes", "no"):
        for price, size in book[side]:
            assert isinstance(price, float)
            assert isinstance(size, float)


def test_get_order_book_empty_ticker():
    client = KalshiClient()
    assert client.get_order_book("") == {"yes": [], "no": []}
    assert client.get_order_book(None) == {"yes": [], "no": []}


def test_get_order_book_network_error_fails_closed():
    client = _client_with_get(None, raise_exc=Exception("connection reset"))
    book = client.get_order_book("SOME-TICKER")
    assert book == {"yes": [], "no": []}


def test_get_order_book_malformed_payload_fails_closed():
    client = _client_with_get({"unexpected": "shape"})
    book = client.get_order_book("SOME-TICKER")
    assert book == {"yes": [], "no": []}


def test_get_order_book_malformed_levels_are_skipped_not_fatal():
    payload = {
        "orderbook_fp": {
            "yes_dollars": [["0.05", "10.0"], ["bad", "data"], None],
            "no_dollars": [],
        }
    }
    client = _client_with_get(payload)
    book = client.get_order_book("SOME-TICKER")
    assert book["yes"] == [(0.05, 10.0)]
    assert book["no"] == []
