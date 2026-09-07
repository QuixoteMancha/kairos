"""Tests de `reverse_geocode` (nominatim_geocoder.py) -- 2026-09-06,
construido para auditar coordenadas de torneo ya geocodificadas sin
país (ver `scripts/tennis/audit_tournament_geocoding.py`)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from outer_apis.scrapers.tennis.nominatim_geocoder import geocode_city, reverse_geocode


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
        "class": "boundary", "type": "administrative", "place_rank": 12,
        "address": {"state_district": "Central Tigray", "country": "Ethiopia", "country_code": "et"},
        "display_name": "Central Tigray, Ethiopia",
    }
    with patch("requests.get", return_value=_fake_response(payload=payload)):
        result = reverse_geocode(13.881273, 39.127495)

    assert result == {
        "country": "Ethiopia", "country_code": "ET",
        "display_name": "Central Tigray, Ethiopia",
        "address": {"state_district": "Central Tigray", "country": "Ethiopia", "country_code": "et"},
        "class": "boundary", "type": "administrative", "place_rank": 12,
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


def test_geocode_city_skips_broad_boundary_and_selects_local_settlement():
    """Caso real: la primera Brescia era la provincia (rank 12), no ciudad."""
    payload = [
        {
            "display_name": "Brescia, Lombardia, Italia",
            "lat": "45.7795805", "lon": "10.4258730",
            "class": "boundary", "type": "administrative", "place_rank": 12,
            "address": {"county": "Brescia", "country": "Italia", "country_code": "it"},
        },
        {
            "display_name": "Brescia, Lombardia, Italia",
            "lat": "45.5398022", "lon": "10.2200214",
            "class": "boundary", "type": "administrative", "place_rank": 16,
            "address": {"city": "Brescia", "country": "Italia", "country_code": "it"},
        },
    ]
    with patch("requests.get", return_value=_fake_response(payload=payload)) as get:
        result = geocode_city("Brescia", country="Italy")

    assert result is not None
    assert (result["lat"], result["lon"]) == (45.5398022, 10.2200214)
    assert result["candidate_rank"] == 2
    assert get.call_args.kwargs["params"] == {
        "format": "json", "limit": 10, "addressdetails": 1,
        "q": "Brescia, Italy",
    }


def test_geocode_city_prefers_place_over_administrative_boundary():
    payload = [
        {
            "lat": "42.3438932", "lon": "-7.8571773",
            "class": "boundary", "type": "administrative", "place_rank": 16,
            "address": {"municipality": "Ourense", "country": "España", "country_code": "es"},
        },
        {
            "lat": "42.3436415", "lon": "-7.8674242",
            "class": "place", "type": "city", "place_rank": 16,
            "address": {"city": "Ourense", "country": "España", "country_code": "es"},
        },
    ]
    with patch("requests.get", return_value=_fake_response(payload=payload)):
        result = geocode_city("Ourense", country="Spain")

    assert result is not None
    assert (result["lat"], result["lon"]) == (42.3436415, -7.8674242)
    assert result["class"] == "place"


def test_geocode_city_rejects_natural_feature_and_nonlocal_boundary():
    payload = [
        {
            "lat": "42.0", "lon": "13.0", "class": "natural", "type": "peak",
            "place_rank": 22, "address": {"county": "L'Aquila", "country": "Italia"},
        },
        {
            "lat": "42.1", "lon": "13.1", "class": "boundary", "type": "administrative",
            "place_rank": 12, "address": {"county": "L'Aquila", "country": "Italia"},
        },
    ]
    with patch("requests.get", return_value=_fake_response(payload=payload)):
        assert geocode_city("L'Aquila", country="Italy") is None


def test_geocode_city_rejects_same_named_settlement_in_different_country():
    """El nombre de ciudad por sí solo nunca compensa otro country_code."""
    payload = [{
        "lat": "39.4929853", "lon": "-117.0714484",
        "class": "place", "type": "village", "place_rank": 19,
        "address": {"village": "Austin", "country": "United States", "country_code": "us"},
    }]
    with patch("requests.get", return_value=_fake_response(payload=payload)):
        assert geocode_city("Austin", country="Australia") is None
