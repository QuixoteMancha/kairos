"""Tests de verificación de identidad real en `search_player`
(flashscore_scraper.py) -- 2026-08-10, pedido explícito del usuario
("aplicar la verificación de nombre tanto cuando el scraper usa proxy
como cuando no") tras encontrar en vivo que `search_player` confiaba
ciegamente en el PRIMER resultado de la búsqueda de Flashscore, sin
verificar que fuera la persona real (7 candidatos "Elina ___" reales
para "Elina Svitolina", tomaba el primero sin chequear -- acertó por
casualidad esa vez, pero el mecanismo era inseguro). Mockea toda
request HTTP real."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

from outer_apis.scrapers.tennis.flashscore_scraper import (
    search_player, _verify_flashscore_player, _player_id_cache,
)


def _fake_response(status_code=200, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


def _profile_html(displayed_name: str) -> str:
    return f'<div class="heading__name">{displayed_name}</div>'


def test_verify_flashscore_player_true_when_name_matches():
    with patch(
        "requests.get",
        return_value=_fake_response(text=_profile_html("Elina Svitolina")),
    ), patch("outer_apis.scrapers.tennis.flashscore_scraper.flashscore_throttle.wait"):
        assert _verify_flashscore_player("jyngIesG", "monfils-elina", "Elina Svitolina") is True


def test_verify_flashscore_player_false_when_name_does_not_match():
    # el caso real que motivó esto: candidato equivocado, mismo nombre
    # de pila pero apellido real distinto.
    with patch(
        "requests.get",
        return_value=_fake_response(text=_profile_html("Elina Zakharova")),
    ), patch("outer_apis.scrapers.tennis.flashscore_scraper.flashscore_throttle.wait"):
        assert _verify_flashscore_player("MX0lkYlJ", "elina-zakharova", "Elina Svitolina") is False


def test_verify_flashscore_player_fail_closed_on_http_error():
    with patch(
        "requests.get",
        return_value=_fake_response(status_code=404, text=""),
    ), patch("outer_apis.scrapers.tennis.flashscore_scraper.flashscore_throttle.wait"):
        assert _verify_flashscore_player("x", "y", "Elina Svitolina") is False


def test_verify_flashscore_player_fail_closed_on_missing_heading():
    with patch(
        "requests.get",
        return_value=_fake_response(text="<div>no heading here</div>"),
    ), patch("outer_apis.scrapers.tennis.flashscore_scraper.flashscore_throttle.wait"):
        assert _verify_flashscore_player("x", "y", "Elina Svitolina") is False


def test_verify_flashscore_player_fail_closed_on_request_exception():
    with patch(
        "requests.get",
        side_effect=Exception("network down"),
    ), patch("outer_apis.scrapers.tennis.flashscore_scraper.flashscore_throttle.wait"):
        assert _verify_flashscore_player("x", "y", "Elina Svitolina") is False


def _search_json(candidates: list[tuple[str, str]]) -> str:
    import json
    results = [
        {"sport_id": 2, "type": "participants", "id": pid, "url": slug}
        for pid, slug in candidates
    ]
    return f"cb({json.dumps({'results': results})})"


def test_search_player_skips_unverified_candidate_and_picks_next():
    _player_id_cache.clear()
    # 1er candidato (equivocado) no verifica, 2do (correcto) sí -- debe
    # devolver el 2do, no rendirse en el 1ro.
    search_resp = _fake_response(text=_search_json([
        ("MX0lkYlJ", "elina-zakharova"),
        ("jyngIesG", "monfils-elina"),
    ]))

    def fake_get(url, **kwargs):
        if "search" in url:
            return search_resp
        if "elina-zakharova" in url:
            return _fake_response(text=_profile_html("Elina Zakharova"))
        if "monfils-elina" in url:
            return _fake_response(text=_profile_html("Elina Svitolina"))
        return _fake_response(status_code=404)

    with patch(
        "requests.get", side_effect=fake_get,
    ), patch("outer_apis.scrapers.tennis.flashscore_scraper.flashscore_throttle.wait"):
        result = search_player("Elina Svitolina")

    assert result == ("jyngIesG", "monfils-elina")


def test_search_player_returns_none_when_no_candidate_verifies():
    _player_id_cache.clear()
    search_resp = _fake_response(text=_search_json([("MX0lkYlJ", "elina-zakharova")]))

    def fake_get(url, **kwargs):
        if "search" in url:
            return search_resp
        return _fake_response(text=_profile_html("Elina Zakharova"))

    with patch(
        "requests.get", side_effect=fake_get,
    ), patch("outer_apis.scrapers.tennis.flashscore_scraper.flashscore_throttle.wait"):
        result = search_player("Elina Svitolina")

    assert result is None
