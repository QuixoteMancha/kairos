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


@pytest.mark.parametrize("real_country,geo_country", [
    ("USA", "United States"),
    ("United States", "USA"),
    ("Great Britain", "United Kingdom"),
    ("Dominican Rep.", "Dominican Republic"),
    ("Hong Kong", "China"),
    ("China", "Hong Kong"),
])
def test_does_not_reset_on_known_country_name_aliases(real_country, geo_country):
    """2026-09-06, bug real: la primera corrida contra producción reseteó
    23/26 filas por esto -- mismo país, nombre distinto, no un homónimo
    real en otro continente."""
    _t("Alias Open", 1.0, 2.0)
    with patch(
        "scripts.tennis.audit_tournament_geocoding.get_tournament_info",
        return_value={"city": "X", "country": real_country, "winner_points": None, "level": None},
    ), patch(
        "scripts.tennis.audit_tournament_geocoding.reverse_geocode",
        return_value={"country": geo_country, "country_code": "XX", "display_name": "x"},
    ):
        run_audit()

    row = Tournament.get(Tournament.tournament_name == "Alias Open")
    assert row.lat == 1.0, f"no debería resetear {real_country!r} vs {geo_country!r} (mismo país)"
    assert row.country == real_country


def test_still_resets_on_genuine_homonym_mismatch():
    """Contraparte real confirmada: Córdoba (España real de TE) vs
    Argentina (donde la coordenada guardada realmente cae) SÍ debe
    resetear -- es un homónimo genuino, no un alias de nombre."""
    _t("Cordoba challenger", 1.0, 2.0)
    with patch(
        "scripts.tennis.audit_tournament_geocoding.get_tournament_info",
        return_value={"city": "Cordoba", "country": "Spain", "winner_points": None, "level": None},
    ), patch(
        "scripts.tennis.audit_tournament_geocoding.reverse_geocode",
        return_value={"country": "Argentina", "country_code": "AR", "display_name": "Cordoba, Argentina"},
    ):
        run_audit()

    row = Tournament.get(Tournament.tournament_name == "Cordoba challenger")
    assert row.lat is None


@pytest.mark.parametrize("real_country,geo_country", [
    ("England", "United Kingdom"),
    ("Scotland", "United Kingdom"),
    ("Czechia", "Czech Republic"),
    ("Czech Republic", "Czechia"),
    ("Chinese Taipei", "Taiwan"),
])
def test_does_not_reset_on_second_round_country_aliases(real_country, geo_country):
    """2026-09-06, ronda 2: 9 de 15 "MAL" del primer lote de control
    contra producción eran esto (England/UK, Czechia/Czech Republic,
    Chinese Taipei/Taiwan), no homónimos reales."""
    _t("Alias Open 2", 1.0, 2.0)
    with patch(
        "scripts.tennis.audit_tournament_geocoding.get_tournament_info",
        return_value={"city": "X", "country": real_country, "winner_points": None, "level": None},
    ), patch(
        "scripts.tennis.audit_tournament_geocoding.reverse_geocode",
        return_value={"country": geo_country, "country_code": "XX", "display_name": "x"},
    ):
        run_audit()

    row = Tournament.get(Tournament.tournament_name == "Alias Open 2")
    assert row.lat == 1.0, f"no debería resetear {real_country!r} vs {geo_country!r} (mismo país)"


@pytest.mark.parametrize("name,lat,lon,real_country,geo_country", [
    ("Tampico WTA", 41.63248, -89.78504, "Mexico", "United States"),
    ("Salinas ITF", 36.67441, -121.65504, "Ecuador", "United States"),
    ("Merida", 38.917465, -6.3443975, "Mexico", "Spain"),
    ("Colina ITF", -20.711443, -48.53867, "Chile", "Brazil"),
])
def test_still_resets_on_real_homonyms_confirmed_in_production(name, lat, lon, real_country, geo_country):
    """Los 6 casos genuinos confirmados en el lote de control real deben
    seguir reseteando incluso con la tabla de alias ampliada."""
    _t(name, lat, lon)
    with patch(
        "scripts.tennis.audit_tournament_geocoding.get_tournament_info",
        return_value={"city": name, "country": real_country, "winner_points": None, "level": None},
    ), patch(
        "scripts.tennis.audit_tournament_geocoding.reverse_geocode",
        return_value={"country": geo_country, "country_code": "XX", "display_name": "x"},
    ):
        run_audit()

    assert Tournament.get(Tournament.tournament_name == name).lat is None
