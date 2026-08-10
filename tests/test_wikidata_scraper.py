"""Tests de `wikidata_scraper.py` -- 2026-08-10, pedido explícito del
usuario: verificar jugadores/as con `tour` ambiguo vía Wikidata (fuente
externa independiente) en vez de seguir hiteando TennisExplorer.
Verificado en vivo contra la API real (Carlos Alcaraz -> ATP, Iga
Swiatek -> WTA, Marin Cilic/Sebastian Baez/Jenson Brooksby -> ATP,
nombre inexistente -> None) antes de escribir estos tests -- acá se
mockea la red, nunca se pega a Wikidata en el suite."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

from outer_apis.scrapers.tennis.wikidata_scraper import get_tennis_player_tour_by_gender


def _fake_response(status_code=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    return resp


def _sparql_result(gender_label: str):
    return {"results": {"bindings": [{"genderLabel": {"value": gender_label}}]}}


def test_returns_atp_for_male():
    with patch(
        "outer_apis.scrapers.tennis.wikidata_scraper.requests.get",
        return_value=_fake_response(json_data=_sparql_result("male")),
    ), patch("outer_apis.scrapers.tennis.wikidata_scraper.wikidata_throttle.wait"):
        assert get_tennis_player_tour_by_gender("Carlos Alcaraz") == "ATP"


def test_returns_wta_for_female():
    with patch(
        "outer_apis.scrapers.tennis.wikidata_scraper.requests.get",
        return_value=_fake_response(json_data=_sparql_result("female")),
    ), patch("outer_apis.scrapers.tennis.wikidata_scraper.wikidata_throttle.wait"):
        assert get_tennis_player_tour_by_gender("Iga Swiatek") == "WTA"


def test_fail_closed_when_no_bindings():
    with patch(
        "outer_apis.scrapers.tennis.wikidata_scraper.requests.get",
        return_value=_fake_response(json_data={"results": {"bindings": []}}),
    ), patch("outer_apis.scrapers.tennis.wikidata_scraper.wikidata_throttle.wait"):
        assert get_tennis_player_tour_by_gender("Nobody Real") is None


def test_fail_closed_on_http_error():
    with patch(
        "outer_apis.scrapers.tennis.wikidata_scraper.requests.get",
        return_value=_fake_response(status_code=500),
    ), patch("outer_apis.scrapers.tennis.wikidata_scraper.wikidata_throttle.wait"):
        assert get_tennis_player_tour_by_gender("Someone") is None


def test_fail_closed_on_request_exception():
    import requests

    with patch(
        "outer_apis.scrapers.tennis.wikidata_scraper.requests.get",
        side_effect=requests.RequestException("timeout"),
    ), patch("outer_apis.scrapers.tennis.wikidata_scraper.wikidata_throttle.wait"):
        assert get_tennis_player_tour_by_gender("Someone") is None


def test_fail_closed_on_unrecognized_gender_value():
    # Wikidata soporta más valores de P21 además de male/female -- este
    # mapeo binario ATP/WTA nunca fuerza una clasificación que no
    # corresponde.
    with patch(
        "outer_apis.scrapers.tennis.wikidata_scraper.requests.get",
        return_value=_fake_response(json_data=_sparql_result("intersex")),
    ), patch("outer_apis.scrapers.tennis.wikidata_scraper.wikidata_throttle.wait"):
        assert get_tennis_player_tour_by_gender("Someone") is None


def test_fail_closed_on_empty_name():
    assert get_tennis_player_tour_by_gender("") is None
    assert get_tennis_player_tour_by_gender("   ") is None


def test_uses_real_throttle_and_user_agent():
    with patch(
        "outer_apis.scrapers.tennis.wikidata_scraper.requests.get",
        return_value=_fake_response(json_data=_sparql_result("male")),
    ) as mock_get, patch(
        "outer_apis.scrapers.tennis.wikidata_scraper.wikidata_throttle.wait",
    ) as mock_wait:
        get_tennis_player_tour_by_gender("Carlos Alcaraz")

    mock_wait.assert_called_once()
    _, kwargs = mock_get.call_args
    assert "User-Agent" in kwargs["headers"]
    assert "Carlos Alcaraz" in kwargs["params"]["query"]
