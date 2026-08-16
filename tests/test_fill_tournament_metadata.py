"""Tests de derivación de nivel y geocoding de torneos -- 2026-08-16,
pedido explícito del usuario ("añade las mejores propuestas, el geocode
y el level")."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis import Tournament
from scripts.tennis.fill_tournament_metadata import (
    derive_tournament_level, fill_tournament_levels, fill_tournament_locations,
)


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        Tournament.delete().execute()
    yield
    with db_historical.connection_context():
        Tournament.delete().execute()


def _t(name: str, **overrides) -> Tournament:
    base = {
        "year": 2025, "tournament_name": name, "city": "", "tour": "ATP",
        "fetched_at": datetime.now(timezone.utc),
    }
    base.update(overrides)
    return Tournament.create(**base)


# --- derive_tournament_level (sin red) ---

@pytest.mark.parametrize("name,tour,expected", [
    # El sufijo de TennisExplorer ES el nivel real (verificado contra prod).
    ("Asuncion challenger", "ATP", "Challenger"),
    ("Sevilla challenger", "ATP", "Challenger"),
    ("Vitoria-Gasteiz ITF", "WTA", "ITF"),
    # Tabla estática de niveles altos, estables año a año.
    ("Wimbledon", "ATP", "Grand Slam"),
    ("Roland Garros", "WTA", "Grand Slam"),
    ("Miami", "ATP", "Masters 1000"),
    ("Cincinnati", "ATP", "Masters 1000"),
    # Circuito principal, nivel exacto desconocido -- honesto, y ya
    # sirve para el filtro "250+".
    ("Santiago", "ATP", "ATP Tour"),
    ("Adelaide", "WTA", "WTA Tour"),
])
def test_derive_level_real_cases(name, tour, expected):
    assert derive_tournament_level(name, tour) == expected


@pytest.mark.parametrize("name", [
    "Bundesliga - men", "Davis Cup", "Billie Jean King Cup", "United Cup",
])
def test_derive_level_fail_closed_on_team_events(name):
    """Eventos de equipo/liga no tienen sede ni nivel de circuito --
    nunca se les asigna uno."""
    assert derive_tournament_level(name, "ATP") is None


def test_derive_level_fail_closed_without_tour():
    assert derive_tournament_level("Santiago", None) is None
    assert derive_tournament_level("", "ATP") is None


def test_derive_level_keyword_matches_as_token_not_substring():
    """"challenger"/"itf" se buscan como palabra completa -- una ciudad
    que las contenga como substring no debe dar un falso positivo."""
    assert derive_tournament_level("Challengerville Open", "ATP") == "ATP Tour"


# --- fill_tournament_levels ---

def test_fill_levels_updates_only_missing():
    _t("Asuncion challenger")
    _t("Wimbledon")
    _t("Ya Tiene", level="Grand Slam")

    result = fill_tournament_levels()

    assert result["updated"] == 2
    assert Tournament.get(Tournament.tournament_name == "Asuncion challenger").level == "Challenger"
    assert Tournament.get(Tournament.tournament_name == "Wimbledon").level == "Grand Slam"


def test_fill_levels_never_downgrades():
    """Nunca degradar: si la fila ya tiene un nivel de mayor jerarquía,
    se conserva (mismo criterio que el backfill oficial WTA, que nació
    de un bug real de degradación silenciosa)."""
    _t("Miami", level="Grand Slam")  # jerarquía mayor que Masters 1000
    fill_tournament_levels()
    assert Tournament.get(Tournament.tournament_name == "Miami").level == "Grand Slam"


def test_fill_levels_skips_team_events():
    _t("Bundesliga - men")
    result = fill_tournament_levels()
    assert result["updated"] == 0
    assert Tournament.get(Tournament.tournament_name == "Bundesliga - men").level is None


# --- fill_tournament_locations ---

def test_fill_locations_prefers_static_table_without_network():
    _t("Cincinnati")
    with patch(
        "scripts.tennis.fill_tournament_metadata.get_location",
        return_value=(39.1, -84.5),
    ), patch("scripts.tennis.fill_tournament_metadata.geocode_city") as geo:
        result = fill_tournament_locations(limit=5)

    geo.assert_not_called(), "no debe gastar un request si la sede ya es conocida"
    assert result["updated"] == 1
    row = Tournament.get(Tournament.tournament_name == "Cincinnati")
    assert (row.lat, row.lon) == (39.1, -84.5)


def test_fill_locations_falls_back_to_nominatim():
    _t("Asuncion challenger")
    with patch(
        "scripts.tennis.fill_tournament_metadata.get_location", return_value=None,
    ), patch(
        "scripts.tennis.fill_tournament_metadata.geocode_city",
        return_value={"lat": -25.3, "lon": -57.6, "display_name": "Asuncion"},
    ) as geo:
        result = fill_tournament_locations(limit=5)

    geo.assert_called_once()
    assert result["updated"] == 1
    row = Tournament.get(Tournament.tournament_name == "Asuncion challenger")
    assert (row.lat, row.lon) == (-25.3, -57.6)
    # `extract_city_candidate` normaliza a minúsculas/sin acentos -- se
    # guarda esa forma canónica, consistente con la clave que ya usa
    # `get_location` internamente.
    assert row.city == "asuncion"


def test_fill_locations_fail_closed_when_not_found():
    _t("Ciudad Inexistente XYZ challenger")
    with patch(
        "scripts.tennis.fill_tournament_metadata.get_location", return_value=None,
    ), patch(
        "scripts.tennis.fill_tournament_metadata.geocode_city", return_value=None,
    ):
        result = fill_tournament_locations(limit=5)

    assert result["updated"] == 0
    assert result["no_encontrados"] == 1
    row = Tournament.get(Tournament.tournament_name == "Ciudad Inexistente XYZ challenger")
    assert row.lat is None, "nunca una sede aproximada"


def test_fill_locations_skips_team_events_without_spending_requests():
    _t("Davis Cup")
    with patch("scripts.tennis.fill_tournament_metadata.geocode_city") as geo:
        result = fill_tournament_locations(limit=5)

    geo.assert_not_called()
    assert result["sin_ciudad_extraible"] == 1
    assert Tournament.get(Tournament.tournament_name == "Davis Cup").lat is None


def test_fill_locations_respects_limit():
    for i in range(5):
        _t(f"Ciudad{i} challenger")
    with patch(
        "scripts.tennis.fill_tournament_metadata.get_location", return_value=None,
    ), patch(
        "scripts.tennis.fill_tournament_metadata.geocode_city",
        return_value={"lat": 1.0, "lon": 2.0, "display_name": "x"},
    ) as geo:
        fill_tournament_locations(limit=2)

    assert geo.call_count == 2
