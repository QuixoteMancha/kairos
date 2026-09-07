from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scripts.tennis.audit_tournament_settlement_geocoding import audit, haversine_km


def _tournament(**overrides):
    values = {
        "id": 1, "year": 2026, "tournament_name": "Brescia ITF",
        "city": "Brescia", "country": "Italy", "lat": 45.7795805,
        "lon": 10.4258730, "elevation_m": 1149,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_haversine_km_is_zero_at_same_coordinate():
    assert haversine_km(-33.45, -70.66, -33.45, -70.66) == 0


def test_audit_reports_far_coordinate_with_independent_elevation_evidence():
    reference = {
        "lat": 45.5398022, "lon": 10.2200214, "display_name": "Brescia, Italy",
        "class": "boundary", "type": "administrative", "place_rank": 16,
        "candidate_rank": 2,
    }
    with patch(
        "scripts.tennis.audit_tournament_settlement_geocoding.geocode_city", return_value=reference,
    ), patch(
        "scripts.tennis.audit_tournament_settlement_geocoding.open_meteo.get_elevation", return_value=149,
    ), patch("scripts.tennis.audit_tournament_settlement_geocoding.open_meteo.throttle_sleep"), patch(
        "scripts.tennis.audit_tournament_settlement_geocoding.reverse_geocode",
        return_value={"address": {"town": "Ponte di Legno", "country": "Italy"}},
    ):
        report = audit([_tournament()])

    assert report["mode"] == "read_only"
    assert report["summary"]["coordinate_groups_far_from_settlement"] == 1
    assert report["summary"]["high_confidence_coordinate_and_elevation_mismatches"] == 1
    evidence = report["candidates"][0]["evidence"][0]
    assert evidence["distance_to_verified_settlement_km"] > 10
    assert evidence["max_elevation_delta_m"] == 1000
    assert evidence["high_confidence"] is True
    assert evidence["stored_coordinate_locality_matches"] is False
    assert evidence["verified_wrong_coordinate"] is True
    assert report["summary"]["verified_wrong_coordinate_groups"] == 1


def test_audit_leaves_unverified_settlement_unresolved_without_elevation_call():
    with patch(
        "scripts.tennis.audit_tournament_settlement_geocoding.geocode_city", return_value=None,
    ), patch("scripts.tennis.audit_tournament_settlement_geocoding.open_meteo.get_elevation") as elevation:
        report = audit([_tournament()])

    assert report["summary"]["no_safe_settlement_result"] == 1
    assert report["candidates"] == []
    assert report["unresolved"][0]["city"] == "Brescia"
    elevation.assert_not_called()


def test_audit_does_not_verify_a_high_candidate_without_reverse_evidence():
    reference = {
        "lat": 45.5398022, "lon": 10.2200214, "display_name": "Brescia, Italy",
        "class": "boundary", "type": "administrative", "place_rank": 16,
        "candidate_rank": 2,
    }
    with patch(
        "scripts.tennis.audit_tournament_settlement_geocoding.geocode_city", return_value=reference,
    ), patch(
        "scripts.tennis.audit_tournament_settlement_geocoding.open_meteo.get_elevation", return_value=149,
    ), patch("scripts.tennis.audit_tournament_settlement_geocoding.open_meteo.throttle_sleep"), patch(
        "scripts.tennis.audit_tournament_settlement_geocoding.reverse_geocode", return_value=None,
    ):
        report = audit([_tournament()])

    evidence = report["candidates"][0]["evidence"][0]
    assert evidence["high_confidence"] is True
    assert evidence["stored_coordinate_locality_matches"] is None
    assert evidence["verified_wrong_coordinate"] is False


def test_audit_does_not_flag_nearby_venue_or_query_elevation():
    row = _tournament(lat=45.540, lon=10.221, elevation_m=150)
    reference = {
        "lat": 45.5398022, "lon": 10.2200214, "display_name": "Brescia, Italy",
        "class": "boundary", "type": "administrative", "place_rank": 16,
        "candidate_rank": 2,
    }
    with patch(
        "scripts.tennis.audit_tournament_settlement_geocoding.geocode_city", return_value=reference,
    ), patch("scripts.tennis.audit_tournament_settlement_geocoding.open_meteo.get_elevation") as elevation:
        report = audit([row])

    assert report["candidates"] == []
    elevation.assert_not_called()
