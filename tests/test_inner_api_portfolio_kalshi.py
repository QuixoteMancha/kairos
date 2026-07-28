"""Tests de GET /api/portfolio/kalshi en inner_api/server.py.

Mockea KalshiPortfolioAPI a nivel del módulo inner_api.server (donde se
importó el símbolo), nunca pega contra Kalshi real. Verifica el shape de
la respuesta y el mapeo de excepciones -> código HTTP."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from inner_api.server import app
from outer_apis.kalshi.portfolio import KalshiAuthError, KalshiCredentialsError


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_portfolio_kalshi_happy_path_shape(client):
    fake_balance = {"balance": 12345, "balance_dollars": "123.45"}
    fake_positions = {"market_positions": [{"ticker": "FOO"}], "event_positions": []}
    fake_fills = [{"fill_id": "1", "ticker": "FOO", "yes_price_dollars": "0.55"}]

    with patch("inner_api.server.KalshiPortfolioAPI") as MockClient:
        instance = MockClient.return_value
        instance.get_portfolio_balance.return_value = fake_balance
        instance.get_portfolio_positions.return_value = fake_positions
        instance.get_portfolio_fills.return_value = fake_fills

        resp = client.get("/api/portfolio/kalshi")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["balance"] == fake_balance
    assert data["positions"] == fake_positions
    assert data["fills"] == fake_fills
    # No debe recalcular PnL/equity del lado del servidor -- fills crudos.
    assert "equity_curve" not in data


def test_portfolio_kalshi_forwards_ts_range(client):
    with patch("inner_api.server.KalshiPortfolioAPI") as MockClient:
        instance = MockClient.return_value
        instance.get_portfolio_balance.return_value = {}
        instance.get_portfolio_positions.return_value = {"market_positions": [], "event_positions": []}
        instance.get_portfolio_fills.return_value = []

        client.get("/api/portfolio/kalshi?min_ts=100&max_ts=200")

        instance.get_portfolio_fills.assert_called_once_with(min_ts=100, max_ts=200)


def test_portfolio_kalshi_missing_credentials_returns_400(client):
    with patch("inner_api.server.KalshiPortfolioAPI") as MockClient:
        MockClient.return_value.get_portfolio_balance.side_effect = KalshiCredentialsError(
            "KALSHI_API_KEY_ID y/o KALSHI_PRIVATE_KEY no están seteadas"
        )
        resp = client.get("/api/portfolio/kalshi")

    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_portfolio_kalshi_auth_error_returns_401(client):
    with patch("inner_api.server.KalshiPortfolioAPI") as MockClient:
        MockClient.return_value.get_portfolio_balance.side_effect = KalshiAuthError(
            "Kalshi rechazó la autenticación (401)"
        )
        resp = client.get("/api/portfolio/kalshi")

    assert resp.status_code == 401
    assert "error" in resp.get_json()


def test_portfolio_kalshi_unexpected_error_returns_500(client):
    with patch("inner_api.server.KalshiPortfolioAPI") as MockClient:
        MockClient.return_value.get_portfolio_balance.side_effect = RuntimeError("boom")
        resp = client.get("/api/portfolio/kalshi")

    assert resp.status_code == 500
    assert "error" in resp.get_json()
