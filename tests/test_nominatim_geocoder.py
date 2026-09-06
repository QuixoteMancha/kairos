"""Tests de `reverse_geocode` (nominatim_geocoder.py) -- 2026-09-06,
construido para auditar coordenadas de torneo ya geocodificadas sin
país (ver `scripts/tennis/audit_tournament_geocoding.py`)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from outer_apis.scrapers.tennis.nominatim_geocoder import reverse_geocode


@pytest.fixture(autouse=True)
def _no_real_throttle_delay():
    # `_throttle()` respeta 1.1s reales entre requests (política dura de
    # Nominatim) -- correcto en producción, pero no hace falta pagar ese
    # costo real en cada test.
    with patch("outer_apis.scrapers.tennis.nominatim_geocoder._throttle"):
        yield


def _fake_response(status_code=200, payload=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload or {}
    return resp


def test_reverse_geocode_parses_real_response_shape():
    """Formato real capturado en vivo -- coordenada de "Tigre challenger"
    mal geocodificada resolvía a Tigray, Etiopía."""
    payload = {
        "address": {"state_district": "Central Tigray", "country": "Ethiopia", "country_code": "et"},
        "display_name": "Central Tigray, Ethiopia",
    }
    with patch("requests.get", return_value=_fake_response(payload=payload)):
        result = reverse_geocode(13.881273, 39.127495)

    assert result == {
        "country": "Ethiopia", "country_code": "ET",
        "display_name": "Central Tigray, Ethiopia",
    }


def test_reverse_geocode_fail_closed_on_http_error():
    with patch("requests.get", return_value=_fake_response(status_code=500)):
        assert reverse_geocode(0, 0) is None


def test_reverse_geocode_fail_closed_on_missing_country():
    with patch("requests.get", return_value=_fake_response(payload={"address": {}})):
        assert reverse_geocode(0, 0) is None


def test_reverse_geocode_fail_closed_on_request_exception():
    with patch("requests.get", side_effect=requests.ConnectionError("network down")):
        assert reverse_geocode(0, 0) is None
