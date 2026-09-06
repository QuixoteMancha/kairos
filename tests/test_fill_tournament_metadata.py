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
    derive_tournament_level, fill_tournament_country, fill_tournament_levels,
    fill_tournament_locations, refine_generic_levels,
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


def test_fill_locations_unresolvable_row_does_not_block_next_cycle():
    # 2026-08-22, bug real encontrado en producción: worker corriendo
    # 24h+ con `updated: 0` en TODOS los ciclos -- un `LIMIT` fijo sin
    # orden/cursor sobre `WHERE lat IS NULL` procesaba SIEMPRE las
    # mismas filas irresolubles (exhibiciones sin ciudad real
    # extraíble), nunca dejando lugar a la cola larga real detrás.
    # Simulado acá con una fila SIN ciudad extraíble (ver
    # `_BLOCKLIST_KEYWORDS`/`extract_city_candidate`) seguida de una
    # resoluble -- el cursor rotativo debe alcanzar la segunda en el
    # ciclo siguiente, no quedarse pegado en la primera para siempre.
    _t("United Cup")  # sin ciudad real extraíble, ver blocklist
    _t("Asuncion challenger")
    with patch(
        "scripts.tennis.fill_tournament_metadata.get_location", return_value=None,
    ), patch(
        "scripts.tennis.fill_tournament_metadata.geocode_city",
        return_value={"lat": -25.3, "lon": -57.6, "display_name": "Asuncion"},
    ):
        first = fill_tournament_locations(limit=1)
        second = fill_tournament_locations(limit=1)

    # la 1ra pasada consume el cupo en la fila irresoluble (sin ciudad);
    # la 2da, gracias al cursor, avanza a la resoluble y la actualiza --
    # sin el fix, ambas pasadas reprocesarían "United Cup" para siempre.
    assert first["sin_ciudad_extraible"] == 1
    assert second["updated"] == 1
    row = Tournament.get(Tournament.tournament_name == "Asuncion challenger")
    assert (row.lat, row.lon) == (-25.3, -57.6)


# --- refine_generic_levels (página de torneo de TennisExplorer) ---

def test_refine_replaces_generic_level_with_exact_one():
    """El genérico "ATP Tour" es transitorio: se refina al nivel exacto
    leído de los puntos de ranking del ganador."""
    _t("Hamburg", level="ATP Tour")
    with patch(
        "scripts.tennis.fill_tournament_metadata.get_tournament_info",
        return_value={"city": "Hamburg", "country": "Germany",
                      "winner_points": 500, "level": "ATP 500"},
    ):
        result = refine_generic_levels(limit=5)

    assert result["refined"] == 1
    row = Tournament.get(Tournament.tournament_name == "Hamburg")
    assert row.level == "ATP 500"
    assert row.city == "Hamburg"
    assert row.country == "Germany"


def test_refine_only_touches_generic_levels():
    """Challenger/ITF ya tienen nivel exacto sin request -- no se gastan
    requests en ellos, ni en los que ya tienen nivel concreto."""
    _t("Asuncion challenger", level="Challenger")
    _t("Miami", level="Masters 1000")
    with patch(
        "scripts.tennis.fill_tournament_metadata.get_tournament_info",
    ) as info:
        result = refine_generic_levels(limit=5)

    info.assert_not_called()
    assert result["processed"] == 0


def test_refine_keeps_generic_when_page_not_found():
    """Fail-closed: si la página no existe (challenger con slug propio,
    torneo raro), se conserva el genérico en vez de romper o inventar."""
    _t("Torneo Raro", level="ATP Tour")
    with patch(
        "scripts.tennis.fill_tournament_metadata.get_tournament_info",
        return_value=None,
    ):
        result = refine_generic_levels(limit=5)

    assert result["not_found"] == 1
    assert Tournament.get(Tournament.tournament_name == "Torneo Raro").level == "ATP Tour"


def test_refine_never_downgrades():
    _t("Wimbledon", level="ATP Tour")
    with patch(
        "scripts.tennis.fill_tournament_metadata.get_tournament_info",
        return_value={"city": "Wimbledon", "country": "Great Britain",
                      "winner_points": None, "level": None},
    ):
        refine_generic_levels(limit=5)

    # Nivel sin dato real -> se conserva el que había; la ciudad/país sí
    # se aprovechan.
    row = Tournament.get(Tournament.tournament_name == "Wimbledon")
    assert row.level == "ATP Tour"
    assert row.country == "Great Britain"


def test_geocoding_uses_city_and_country_when_available():
    """"Santiago" a secas es ambiguo (Chile/Compostela/Cuba) -- con país
    conocido se consulta "Santiago, Chile"."""
    _t("Santiago", city="Santiago", country="Chile")
    with patch(
        "scripts.tennis.fill_tournament_metadata.get_location", return_value=None,
    ), patch(
        "scripts.tennis.fill_tournament_metadata.geocode_city",
        return_value={"lat": -33.4, "lon": -70.6, "display_name": "Santiago"},
    ) as geo:
        fill_tournament_locations(limit=5)

    geo.assert_called_once_with("Santiago, Chile")


# --- fill_tournament_country (2026-09-06, bug real: Challenger/ITF nunca
# recibían country porque refine_generic_levels solo corre para ATP/WTA
# Tour) ---

def test_fill_country_works_for_challenger_level_too():
    """A diferencia de `refine_generic_levels`, esta función SÍ debe
    tocar Challenger/ITF -- son justo los que quedaban sin país."""
    _t("Tigre challenger", level="Challenger", tour="ATP")
    with patch(
        "scripts.tennis.fill_tournament_metadata.get_tournament_info",
        return_value={"city": "Tigre", "country": "Argentina",
                      "winner_points": None, "level": None},
    ) as info:
        result = fill_tournament_country(limit=5)

    info.assert_called_once()
    assert result["refined"] == 1
    row = Tournament.get(Tournament.tournament_name == "Tigre challenger")
    assert row.country == "Argentina"
    assert row.city == "Tigre"
    # No debe tocar el nivel -- ese es el trabajo de otra función.
    assert row.level == "Challenger"


def test_fill_country_skips_rows_with_country_already_known():
    _t("Hamburg", level="ATP 500", country="Germany")
    with patch(
        "scripts.tennis.fill_tournament_metadata.get_tournament_info",
    ) as info:
        result = fill_tournament_country(limit=5)

    info.assert_not_called()
    assert result["processed"] == 0


def test_fill_country_fail_closed_when_page_not_found():
    _t("Torneo Raro", level="Challenger")
    with patch(
        "scripts.tennis.fill_tournament_metadata.get_tournament_info",
        return_value=None,
    ):
        result = fill_tournament_country(limit=5)

    assert result["not_found"] == 1
    assert Tournament.get(Tournament.tournament_name == "Torneo Raro").country is None


def test_fill_country_never_overwrites_existing_country():
    _t("Santiago", level="ATP Tour", country="Chile")
    with patch(
        "scripts.tennis.fill_tournament_metadata.get_tournament_info",
    ) as info:
        fill_tournament_country(limit=5)
    info.assert_not_called()
    assert Tournament.get(Tournament.tournament_name == "Santiago").country == "Chile"
