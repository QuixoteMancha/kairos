"""Tests de `outer_apis/scrapers/baseball/team_location.py` --
2026-08-05, pedido explícito del usuario: adaptar el backfill de clima
de tenis a MLB."""

from __future__ import annotations

from outer_apis.scrapers.baseball.team_location import get_team_location


def test_get_team_location_fail_closed_on_unknown_team():
    assert get_team_location("Nowhereville Sluggers") is None


def test_get_team_location_fail_closed_on_none():
    assert get_team_location(None) is None


def test_get_team_location_known_real_teams():
    for team in ["New York Yankees", "Los Angeles Dodgers", "Boston Red Sox", "Toronto Blue Jays"]:
        assert get_team_location(team) is not None, f"{team!r} debería tener ubicación real"
