"""Tests de `outer_apis/scrapers/tennis/tournament_location.py` --
2026-08-04, pedido explícito del usuario: investigar y priorizar el gap
de clima (90% de `TennisMatchHistory` sin clima) -- el 85% del gap real
era normalización de nombres, no falta de dato."""

from __future__ import annotations

from outer_apis.scrapers.tennis.tournament_location import get_location, _normalize


def test_get_location_fail_closed_on_unknown_tournament():
    assert get_location("Nowhereville Open") is None


def test_get_location_fail_closed_on_none():
    assert get_location(None) is None


def test_get_location_exact_match_case_insensitive():
    assert get_location("Washington") == get_location("washington")


def test_get_location_strips_tour_suffix():
    """Bug real encontrado: 'Rome WTA'/'Cincinnati WTA' etc no
    matcheaban aunque la ciudad YA estaba en la tabla -- el sufijo de
    tour rompía el match exacto."""
    assert get_location("Rome WTA") == get_location("rome")
    assert get_location("Madrid WTA") == get_location("madrid")
    assert get_location("Cincinnati WTA") == get_location("cincinnati")


def test_get_location_strips_challenger_suffix():
    assert get_location("Prostejov challenger") is None  # Prostejov NO está en la tabla (no verificado), pero el sufijo se quita igual
    assert _normalize("Prostejov challenger") == "prostejov"


def test_get_location_strips_gendered_suffix():
    assert get_location("Bundesliga - men") is None  # liga por equipos -- correctamente sin sede única, no se agrega
    assert _normalize("Bundesliga - men") == "bundesliga"


def test_get_location_normalizes_unicode_accents():
    """Bug real encontrado: 'Kitzbühel' (con diéresis real) no
    matcheaba la clave ASCII 'kitzbuhel' de la tabla."""
    assert get_location("Kitzbühel") is not None
    assert get_location("Kitzbühel") == get_location("kitzbuhel")


def test_new_real_cities_added_2026_08_04():
    """Ciudades sede real de torneos ATP/WTA/challenger agregadas tras
    investigar los top ofensores del gap de clima -- verificar que
    quedaron mapeadas (no None)."""
    for name in [
        "Brisbane", "Adelaide", "Charleston", "Auckland", "Linz", "Wuhan",
        "Strasbourg", "Montreal WTA", "Toronto WTA", "Buenos Aires challenger",
        "Bratislava challenger", "Ostrava challenger", "Abu Dhabi WTA",
        "Hertogenbosch", "Livesport Prague Open",
    ]:
        assert get_location(name) is not None, f"{name!r} debería resolver a una ubicación real"


def test_team_league_events_stay_unresolved_no_single_venue():
    """Fail-closed a propósito: eventos de equipos/ligas nacionales NO
    tienen una sede única real, nunca se debe inventar una ciudad para
    ellos aunque aparezcan con volumen alto en el gap."""
    for name in ["Bundesliga - men", "Bundesliga - women", "Czech league", "Davis Cup", "Italian Team Championship"]:
        assert get_location(name) is None
