"""Regression test: GET /api/wallet/platforms must never synthesize a fake
'cross_book' platform card. 'cross_book' is the internal unified-engine
mode marker (state.active_platform's default), not a real platform/wallet —
_build_platform_catalog() used to add it via the `discovered` fallback set
whenever state.active_platform == 'cross_book' (the common case), producing
a nonsensical "Cross-Book" card in the frontend portfolio view with no real
wallet behind it. Found+fixed 2026-07-08 while addressing a user-reported
frontend issue."""
from __future__ import annotations

from src.core import state
from src.web.create_app import create_app


def test_platforms_endpoint_never_includes_cross_book():
    state.active_platform = "cross_book"
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        resp = client.get("/api/wallet/platforms")
        assert resp.status_code == 200
        data = resp.get_json()
        slugs = {p["slug"] for p in data["platforms"]}
        assert "cross_book" not in slugs


def test_build_platform_catalog_never_includes_cross_book_directly():
    from src.web.blueprints.wallet import _build_platform_catalog

    state.active_platform = "cross_book"
    catalog = _build_platform_catalog()
    slugs = {p["slug"] for p in catalog}
    assert "cross_book" not in slugs
