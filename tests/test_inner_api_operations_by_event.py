"""Tests de GET /api/operations/by-event en inner_api/server.py.

Usa la DB SQLite temporal/aislada de tests/conftest.py (nunca la DB real
del repo). Crea filas reales de LimitArbSignal/PaperTrade en vez de
mockear el ORM, para verificar el agrupamiento y la agregación de PnL de
punta a punta."""
from __future__ import annotations

import pytest

from database.db import init_db, db
from database.accounting.limitarbsignal import LimitArbSignal
from database.accounting.papertrade import PaperTrade
from inner_api.server import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def clean_db():
    init_db()
    with db.connection_context():
        PaperTrade.delete().execute()
        LimitArbSignal.delete().execute()
    yield
    with db.connection_context():
        PaperTrade.delete().execute()
        LimitArbSignal.delete().execute()


def _make_signal(market_id: str) -> LimitArbSignal:
    with db.connection_context():
        return LimitArbSignal.create(
            market_id=market_id,
            case="favorite_overvalued",
            avg_volatility=0.1,
            raw_edge=0.05,
            price_adjustment=0.08,
            w_edge=0.4,
            w_volatility=0.6,
            return_opportunity=0.08,
        )


def _make_trade(signal: LimitArbSignal, status: str, realized_pnl_usd: float | None) -> PaperTrade:
    with db.connection_context():
        return PaperTrade.create(
            signal_id=signal.id,
            market_id=signal.market_id,
            case=signal.case,
            position_size_usd=10.0,
            p_success_proxy_at_open=0.6,
            kelly_fraction_used=0.05,
            market_leg_price=0.5,
            market_leg_fee=0.1,
            status=status,
            realized_pnl_usd=realized_pnl_usd,
        )


def test_groups_by_market_id_and_aggregates_pnl(client):
    s1 = _make_signal("KXMLBGAME-FOO")
    _make_trade(s1, status="closed", realized_pnl_usd=5.0)  # win
    s2 = _make_signal("KXMLBGAME-FOO")
    _make_trade(s2, status="closed", realized_pnl_usd=-2.0)  # loss, mismo evento
    s3 = _make_signal("KXNBAGAME-BAR")
    _make_trade(s3, status="open", realized_pnl_usd=None)  # todavía abierto

    resp = client.get("/api/operations/by-event")
    assert resp.status_code == 200
    by_market = {d["market_id"]: d for d in resp.get_json()}

    foo = by_market["KXMLBGAME-FOO"]
    assert foo["n_operations"] == 2
    assert foo["n_wins"] == 1
    assert foo["n_losses"] == 1
    assert foo["n_open"] == 0
    assert foo["total_realized_pnl_usd"] == pytest.approx(3.0)
    assert len(foo["operations"]) == 2

    bar = by_market["KXNBAGAME-BAR"]
    assert bar["n_operations"] == 1
    assert bar["n_open"] == 1
    assert bar["n_wins"] == 0
    assert bar["n_losses"] == 0
    assert bar["total_realized_pnl_usd"] == pytest.approx(0.0)


def test_signal_without_trade_counts_as_open(client):
    _make_signal("KXATP-SOMEMATCH")  # sin PaperTrade asociado (nunca se abrió posición)

    resp = client.get("/api/operations/by-event")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) == 1
    assert data[0]["market_id"] == "KXATP-SOMEMATCH"
    assert data[0]["n_open"] == 1
    assert data[0]["operations"][0]["paper_trade"] is None


def test_market_id_filter_on_list_operations(client):
    s1 = _make_signal("KXMLBGAME-FOO")
    _make_signal("KXMLBGAME-OTHER")

    resp = client.get("/api/operations?market_id=KXMLBGAME-FOO")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) == 1
    assert data[0]["id"] == s1.id
    assert data[0]["market_id"] == "KXMLBGAME-FOO"
