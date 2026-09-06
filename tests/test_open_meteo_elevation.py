"""Tests de `get_elevation` (open_meteo.py) -- 2026-09-06, pedido
explícito del usuario sobre el impacto de la altitud de la sede."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests

from outer_apis.scrapers.weather.open_meteo import get_elevation


def _fake_response(status_code=200, payload=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload or {}
    return resp


def test_get_elevation_parses_real_response_shape():
    with patch("requests.get", return_value=_fake_response(payload={
        "latitude": 48.82, "longitude": 2.29, "elevation": 46.0, "hourly": {},
    })):
        assert get_elevation(48.85, 2.35) == 46.0


def test_get_elevation_fail_closed_on_http_error():
    with patch("requests.get", return_value=_fake_response(status_code=500)):
        assert get_elevation(0, 0) is None


def test_get_elevation_fail_closed_on_missing_field():
    with patch("requests.get", return_value=_fake_response(payload={"latitude": 0})):
        assert get_elevation(0, 0) is None


def test_get_elevation_fail_closed_on_request_exception():
    with patch("requests.get", side_effect=requests.ConnectionError("network down")):
        assert get_elevation(0, 0) is None
