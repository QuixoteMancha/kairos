"""
Tests para los endpoints de healthcheck.

Verifican:
- /healthz responde 200 con status=ok.
- /readyz devuelve 200/503 con detalle de componentes.
"""
from __future__ import annotations

import pytest

from src.web.blueprints.health import health_bp


@pytest.fixture
def client():
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(health_bp)
    return app.test_client()


def test_healthz_always_ok(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert "ts" in data


def test_readyz_returns_components(client):
    resp = client.get("/readyz")
    # 200 si todo bien, 503 si algo degradado — ambos válidos en CI
    assert resp.status_code in (200, 503)
    data = resp.get_json()
    assert "components" in data
    assert "db" in data["components"]
    assert "wallet" in data["components"]
    assert "cross_book_pool" in data["components"]



