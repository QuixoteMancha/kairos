"""Tests de GET /api/portfolio/kalshi en inner_api/server.py.

Mockea KalshiPortfolioAPI a nivel del módulo inner_api.server (donde se
importó el símbolo), nunca pega contra Kalshi real. Verifica el shape de
la respuesta y el mapeo de excepciones -> código HTTP."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from inner_api.server import app, _derive_net_pnl_usd
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


def test_portfolio_kalshi_by_event_groups_fills_and_attaches_kalshi_pnl(client):
    """El PnL de cada evento viene TAL CUAL de Kalshi (realized_pnl_dollars
    en market_positions), no recalculado -- ver docstring de la ruta."""
    fake_positions = {
        "market_positions": [
            {"ticker": "KXMLBGAME-FOO", "realized_pnl_dollars": "12.500000", "position_fp": "0.00"},
            {"ticker": "KXMLBGAME-BAR", "realized_pnl_dollars": "-3.000000", "position_fp": "0.00"},
        ],
        "event_positions": [],
    }
    fake_fills = [
        {"fill_id": "1", "ticker": "KXMLBGAME-FOO", "yes_price_dollars": "0.55"},
        {"fill_id": "2", "ticker": "KXMLBGAME-FOO", "yes_price_dollars": "0.60"},
        {"fill_id": "3", "ticker": "KXMLBGAME-BAR", "yes_price_dollars": "0.30"},
        {"fill_id": "4", "ticker": "KXMLBGAME-NOPOSITION", "yes_price_dollars": "0.40"},
    ]

    with patch("inner_api.server.KalshiPortfolioAPI") as MockClient:
        instance = MockClient.return_value
        instance.get_portfolio_positions.return_value = fake_positions
        instance.get_portfolio_fills.return_value = fake_fills
        instance.get_portfolio_settlements.return_value = []

        resp = client.get("/api/portfolio/kalshi/by-event")

    assert resp.status_code == 200
    data = resp.get_json()
    by_ticker = {d["ticker"]: d for d in data}

    assert len(by_ticker["KXMLBGAME-FOO"]["fills"]) == 2
    assert by_ticker["KXMLBGAME-FOO"]["position"]["realized_pnl_dollars"] == "12.500000"

    assert len(by_ticker["KXMLBGAME-BAR"]["fills"]) == 1
    assert by_ticker["KXMLBGAME-BAR"]["position"]["realized_pnl_dollars"] == "-3.000000"

    # Fail-closed: un ticker con fills pero sin position ya reportada por
    # Kalshi (mercado muy viejo purgado) no inventa un PnL, queda None.
    assert by_ticker["KXMLBGAME-NOPOSITION"]["position"] is None


def test_portfolio_kalshi_by_event_attaches_settlement_for_closed_positions(client):
    """Un mercado YA LIQUIDADO desaparece de `positions` (Kalshi solo
    lista posiciones en pie) -- el resultado real tiene que venir de
    `settlements`, no quedar en None solo porque `positions` ya no lo
    tiene."""
    fake_settlements = [
        {
            "ticker": "KXMLBGAME-CLOSED",
            "market_result": "yes",
            "revenue": 4000,
            "yes_total_cost_dollars": "20.000000",
            "no_total_cost_dollars": "0.000000",
            "fee_cost": "0.800000",
        }
    ]
    fake_fills = [
        {"fill_id": "1", "ticker": "KXMLBGAME-CLOSED", "yes_price_dollars": "0.50"},
    ]

    with patch("inner_api.server.KalshiPortfolioAPI") as MockClient:
        instance = MockClient.return_value
        instance.get_portfolio_positions.return_value = {"market_positions": [], "event_positions": []}
        instance.get_portfolio_fills.return_value = fake_fills
        instance.get_portfolio_settlements.return_value = fake_settlements

        resp = client.get("/api/portfolio/kalshi/by-event")

    assert resp.status_code == 200
    by_ticker = {d["ticker"]: d for d in resp.get_json()}

    closed = by_ticker["KXMLBGAME-CLOSED"]
    assert closed["position"] is None  # ya no está en positions, esperado
    assert closed["settlement"]["market_result"] == "yes"
    assert closed["settlement"]["revenue"] == 4000
    assert len(closed["fills"]) == 1
    # revenue $40.00 - costo yes $20 - costo no $0 - fee $0.80 = $19.20
    assert closed["net_pnl_usd"] == pytest.approx(19.20)


def test_portfolio_kalshi_by_event_includes_settlement_without_fills_in_range(client):
    """Un evento liquidado sin fills en el rango pedido (ej. filtrado por
    min_ts/max_ts angosto) no debe quedar huérfano -- su settlement debe
    aparecer igual."""
    fake_settlements = [
        {"ticker": "KXMLBGAME-OLDCLOSED", "market_result": "no", "revenue": 0},
    ]

    with patch("inner_api.server.KalshiPortfolioAPI") as MockClient:
        instance = MockClient.return_value
        instance.get_portfolio_positions.return_value = {"market_positions": [], "event_positions": []}
        instance.get_portfolio_fills.return_value = []
        instance.get_portfolio_settlements.return_value = fake_settlements

        resp = client.get("/api/portfolio/kalshi/by-event")

    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) == 1
    assert data[0]["ticker"] == "KXMLBGAME-OLDCLOSED"
    assert data[0]["settlement"]["market_result"] == "no"
    assert data[0]["fills"] == []


def test_derive_net_pnl_usd_from_settlement():
    settlement = {
        "revenue": 4000, "yes_total_cost_dollars": "20.000000",
        "no_total_cost_dollars": "0.000000", "fee_cost": "0.800000",
    }
    assert _derive_net_pnl_usd(None, settlement) == pytest.approx(19.20)


def test_derive_net_pnl_usd_from_open_position_uses_realized_pnl_dollars():
    position = {"realized_pnl_dollars": "0.000000"}
    assert _derive_net_pnl_usd(position, None) == pytest.approx(0.0)


def test_derive_net_pnl_usd_none_when_neither_available():
    assert _derive_net_pnl_usd(None, None) is None


def test_derive_net_pnl_usd_fail_closed_on_malformed_settlement():
    """Un settlement con un campo corrupto/inesperado no debe reventar
    todo el endpoint por un solo registro raro -- None, no una excepción
    ni un número fabricado."""
    assert _derive_net_pnl_usd(None, {"revenue": "not-a-number"}) is None


def test_portfolio_kalshi_by_event_missing_credentials_returns_400(client):
    with patch("inner_api.server.KalshiPortfolioAPI") as MockClient:
        MockClient.return_value.get_portfolio_positions.side_effect = KalshiCredentialsError("no creds")
        resp = client.get("/api/portfolio/kalshi/by-event")

    assert resp.status_code == 400
