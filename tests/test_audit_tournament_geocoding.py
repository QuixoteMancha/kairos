"""Tests de `scripts/tennis/audit_tournament_geocoding.py` -- 2026-09-06,
corrige coordenadas de torneo mal geocodificadas por homónimos en otro
continente (caso real: "Tigre challenger" -> Tigray, Etiopía)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from database.db import db_historical, init_db_historical
from database.historical.tennis.models import Tournament
from scripts.tennis.audit_tournament_geocoding import main as run_audit


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        Tournament.delete().execute()
    yield
    with db_historical.connection_context():
        Tournament.delete().execute()


def _t(name, lat, lon, **overrides):
    base = {
        "year": 2025, "tournament_name": name, "city": "tigre", "tour": "ATP",
        "lat": lat, "lon": lon, "fetched_at": datetime.now(timezone.utc),
    }
    base.update(overrides)
    return Tournament.create(**base)


def test_resets_coordinate_when_country_mismatches():
    _t("Tigre challenger", 13.881273, 39.127495)
    with patch(
        "scripts.tennis.audit_tournament_geocoding.get_tournament_info",
        return_value={"city": "Tigre", "country": "Argentina", "winner_points": None, "level": None},
    ), patch(
        "scripts.tennis.audit_tournament_geocoding.reverse_geocode",
        return_value={"country": "Ethiopia", "country_code": "ET", "display_name": "Central Tigray"},
    ):
        run_audit()

    row = Tournament.get(Tournament.tournament_name == "Tigre challenger")
    assert row.country == "Argentina"
    assert row.lat is None
    assert row.lon is None
    assert row.elevation_m is None


def test_keeps_coordinate_when_country_matches():
    _t("Bogota WTA", 4.653382, -74.08363, elevation_m=2553.0)
    with patch(
        "scripts.tennis.audit_tournament_geocoding.get_tournament_info",
        return_value={"city": "Bogota", "country": "Colombia", "winner_points": None, "level": None},
    ), patch(
        "scripts.tennis.audit_tournament_geocoding.reverse_geocode",
        return_value={"country": "Colombia", "country_code": "CO", "display_name": "Bogota, Colombia"},
    ):
        run_audit()

    row = Tournament.get(Tournament.tournament_name == "Bogota WTA")
    assert row.country == "Colombia"
    assert row.lat == 4.653382
    assert row.elevation_m == 2553.0


def test_fail_closed_when_tennisexplorer_has_no_info():
    _t("Torneo Raro", 1.0, 2.0)
    with patch(
        "scripts.tennis.audit_tournament_geocoding.get_tournament_info", return_value=None,
    ), patch(
        "scripts.tennis.audit_tournament_geocoding.reverse_geocode",
    ) as reverse:
        run_audit()

    reverse.assert_not_called()
    row = Tournament.get(Tournament.tournament_name == "Torneo Raro")
    assert row.country is None
    assert row.lat == 1.0  # nunca se resetea sin saber si está mal


def test_fail_closed_when_reverse_geocode_fails():
    _t("Torneo Raro 2", 1.0, 2.0)
    with patch(
        "scripts.tennis.audit_tournament_geocoding.get_tournament_info",
        return_value={"city": "X", "country": "Argentina", "winner_points": None, "level": None},
    ), patch(
        "scripts.tennis.audit_tournament_geocoding.reverse_geocode", return_value=None,
    ):
        run_audit()

    row = Tournament.get(Tournament.tournament_name == "Torneo Raro 2")
    assert row.country is None  # no se guarda nada a medias
    assert row.lat == 1.0


def test_reverse_geocode_cached_per_coordinate():
    """Dos torneos que comparten sede no deben pedir reverse-geocode dos
    veces -- se cachea por coordenada redondeada."""
    _t("Tigre challenger", 13.881273, 39.127495)
    _t("Tigre 2 challenger", 13.881273, 39.127495)
    with patch(
        "scripts.tennis.audit_tournament_geocoding.get_tournament_info",
        return_value={"city": "Tigre", "country": "Argentina", "winner_points": None, "level": None},
    ), patch(
        "scripts.tennis.audit_tournament_geocoding.reverse_geocode",
        return_value={"country": "Ethiopia", "country_code": "ET", "display_name": "Central Tigray"},
    ) as reverse:
        run_audit()

    reverse.assert_called_once()
