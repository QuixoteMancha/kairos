"""
test_odds_movement.py — /api/odds/movement: dirección del signal sharp_p1/sharp_p2.

Bug fijado: el signal estaba invertido — odd_p1 bajando (dinero entrando en
p1) se reportaba como "sharp_p2" y viceversa.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest


@pytest.fixture
def client():
    from flask import Flask
    from src.web.blueprints.backtest import backtest_bp
    from src.database.database import init_db

    init_db()
    app = Flask(__name__)
    app.register_blueprint(backtest_bp)
    return app.test_client()


def _make_snapshot(fixture, odd_p1, hours_ago):
    from datetime import datetime, timedelta
    from src.database.database import OddsSnapshot

    OddsSnapshot.create(
        timestamp=(datetime.now() - timedelta(hours=hours_ago)).isoformat(),
        platform="stake",
        fixture=fixture,
        market_tipo="moneyline",
        linea=None,
        odd_p1=odd_p1,
        odd_p2=2.0,
        overround=1.0,
        source="test",
    )


def test_odd_p1_drop_signals_sharp_p1(client):
    """odd_p1 BAJA (1.90 -> 1.70) = la casa acorta el precio de p1 = dinero
    entrando en p1 -> signal debe ser sharp_p1, NO sharp_p2."""
    from src.database.database import OddsSnapshot
    OddsSnapshot.delete().execute()
    _make_snapshot("Drop FC vs Rise United", odd_p1=1.90, hours_ago=2)
    _make_snapshot("Drop FC vs Rise United", odd_p1=1.70, hours_ago=0)

    resp = client.get("/api/odds/movement", query_string={"platform": "stake", "hours": 24})
    assert resp.status_code == 200
    movements = resp.get_json()["movements"]
    m = next(x for x in movements if x["fixture"] == "Drop FC vs Rise United")
    assert m["delta_p1"] < 0
    assert m["signal"] == "sharp_p1"


def test_odd_p1_rise_signals_sharp_p2(client):
    """odd_p1 SUBE (1.70 -> 1.90) = dinero saliendo de p1 (hacia p2) ->
    signal debe ser sharp_p2, NO sharp_p1."""
    from src.database.database import OddsSnapshot
    OddsSnapshot.delete().execute()
    _make_snapshot("Rise FC vs Drop United", odd_p1=1.70, hours_ago=2)
    _make_snapshot("Rise FC vs Drop United", odd_p1=1.90, hours_ago=0)

    resp = client.get("/api/odds/movement", query_string={"platform": "stake", "hours": 24})
    assert resp.status_code == 200
    movements = resp.get_json()["movements"]
    m = next(x for x in movements if x["fixture"] == "Rise FC vs Drop United")
    assert m["delta_p1"] > 0
    assert m["signal"] == "sharp_p2"
