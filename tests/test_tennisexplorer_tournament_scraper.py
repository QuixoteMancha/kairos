"""Tests de la página de torneo de TennisExplorer -- 2026-08-16, a raíz
de la pregunta del usuario "¿tennis explorer no trae el nombre del
torneo?": sí, y además trae ciudad, país y los puntos de ranking del
ganador, que son el NIVEL exacto del torneo."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from outer_apis.scrapers.tennis.tennisexplorer_tournament_scraper import (
    _parse_winner_points, _slugify, get_tournament_info, level_from_winner_points,
)

# Forma real de la tabla, aplanada como llega (verificada en vivo:
# la moneda varía entre $ y €, por eso no se asume ninguna).
_HTML = """<html><h1>Santiago 2026 (Chile)</h1>
<table><tr><th>Round</th><th>Prize money</th><th>Ranking points</th></tr>
<tr><td>1. round</td><td>7,510 $</td><td>0</td></tr>
<tr><td>final</td><td>62,115 $</td><td>165</td></tr>
<tr><td>winner</td><td>106,460 $</td><td>250</td></tr></table></html>"""


def _resp(text: str, status: int = 200) -> Mock:
    return Mock(status_code=status, text=text)


def test_slugify_handles_spaces_and_accents():
    assert _slugify("Monte Carlo") == "monte-carlo"
    assert _slugify("Vitoria-Gasteiz") == "vitoria-gasteiz"
    assert _slugify("Düsseldorf") == "dusseldorf"


def test_parse_winner_points_from_real_table_shape():
    assert _parse_winner_points(_HTML) == 250


def test_parse_winner_points_accepts_euro_currency():
    """Wimbledon usa €, no $ -- verificado en vivo. El parser no debe
    asumir la moneda."""
    html = _HTML.replace("106,460 $", "3,000,000 €").replace(
        "<td>250</td>", "<td>2,000</td>")
    assert _parse_winner_points(html) == 2000


def test_parse_winner_points_fail_closed_without_table():
    assert _parse_winner_points("<html><h1>X 2025 (Y)</h1></html>") is None


@pytest.mark.parametrize("points,tour,expected", [
    (2000, "ATP", "Grand Slam"),
    (1000, "ATP", "Masters 1000"),
    (500, "ATP", "ATP 500"),
    (250, "ATP", "ATP 250"),
    (1000, "WTA", "WTA 1000"),
    (500, "WTA", "WTA 500"),
    (250, "WTA", "WTA 250"),
])
def test_level_from_winner_points(points, tour, expected):
    assert level_from_winner_points(points, tour) == expected


def test_level_fail_closed_on_unknown_points():
    """Nunca se aproxima al nivel 'más cercano'."""
    assert level_from_winner_points(333, "ATP") is None
    assert level_from_winner_points(None, "ATP") is None


def test_get_tournament_info_parses_city_country_and_level():
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_tournament_scraper.requests.get",
        return_value=_resp(_HTML),
    ), patch("outer_apis.scrapers.tennis.tennisexplorer_tournament_scraper._throttle"):
        info = get_tournament_info("Santiago", 2026, "ATP")

    assert info == {
        "city": "Santiago", "country": "Chile",
        "winner_points": 250, "level": "ATP 250",
    }


def test_get_tournament_info_fail_closed_on_404():
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_tournament_scraper.requests.get",
        return_value=_resp("", status=404),
    ), patch("outer_apis.scrapers.tennis.tennisexplorer_tournament_scraper._throttle"):
        assert get_tournament_info("Torneo Inexistente", 2025, "ATP") is None


def test_get_tournament_info_fail_closed_on_unparseable_h1():
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_tournament_scraper.requests.get",
        return_value=_resp("<html><h1>sin formato esperado</h1></html>"),
    ), patch("outer_apis.scrapers.tennis.tennisexplorer_tournament_scraper._throttle"):
        assert get_tournament_info("X", 2025, "ATP") is None


def test_get_tournament_info_requires_known_tour():
    """Sin tour no se puede construir la categoría de la URL -- no se
    adivina."""
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_tournament_scraper.requests.get",
    ) as get:
        assert get_tournament_info("Santiago", 2026, None) is None
    get.assert_not_called()
