"""
test_flashscore_team_search.py — regresión del bug en search_team_fs.

BUG: search_team_fs() armaba su request reusando _SEARCH_URL, una constante
hardcodeada a sport_id=2 (tenis) con l=1 (un solo resultado). Resultado: la
búsqueda de un EQUIPO (fútbol/básquet/béisbol/hockey/etc.) jamás podía
encontrar nada — el único resultado posible que el endpoint podía devolver
era de tenis, así que el filtro `sport_id == want_sid` nunca daba match para
ningún deporte de equipo. Esto dejaba sin funcionar el fallback Flashscore de
`_settle_stat_bets_by_score` y `_settle_cross_book_bets_by_score` para TODO lo
que no fuera tenis.

Fix: search_team_fs ahora usa _TEAM_SEARCH_URL, parametrizada con el
sport_id real del deporte buscado (vía _FS_SPORT_ID) y un límite mayor (l=8).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import src.scrapers.flashscore_scraper as fs


def _jsonp(results: list[dict]) -> str:
    import json
    return f"cjs.search.jsonpCallback({json.dumps({'results': results})})"


def test_search_team_fs_requests_correct_sport_id_not_tennis():
    """La URL llamada debe pedir el sport_id del deporte buscado, no tenis (2)."""
    fs._team_id_cache.clear()
    captured = {}

    def _fake_get(url, **kwargs):
        captured["url"] = url
        resp = MagicMock()
        resp.text = _jsonp([
            {"id": "ABC123", "type": "participants", "sport_id": 1, "url": "real-team"},
        ])
        return resp

    with patch("curl_cffi.requests.get", side_effect=_fake_get):
        result = fs.search_team_fs("Real Team", "football")

    assert result == ("ABC123", "real-team")
    # sport_id de fútbol en _FS_SPORT_ID es 1 — la URL NO debe llevar sid=2 (tenis)
    assert "sid=1" in captured["url"]
    assert "pid=1" in captured["url"]
    assert "sid=2" not in captured["url"]


def test_search_team_fs_filters_out_wrong_sport_results():
    """Si el endpoint devuelve resultados de OTRO deporte, se ignoran."""
    fs._team_id_cache.clear()

    def _fake_get(url, **kwargs):
        resp = MagicMock()
        resp.text = _jsonp([
            {"id": "TENNIS1", "type": "participants", "sport_id": 2, "url": "wrong-sport"},
            {"id": "BBALL1", "type": "participants", "sport_id": 3, "url": "right-team"},
        ])
        return resp

    with patch("curl_cffi.requests.get", side_effect=_fake_get):
        result = fs.search_team_fs("Some Club", "basketball")

    assert result == ("BBALL1", "right-team")


def test_search_team_fs_unknown_sport_returns_none():
    assert fs.search_team_fs("Anything", "curling") is None


def test_search_player_fs_still_uses_tennis_url():
    """search_player_fs (jugadores de tenis) no debe verse afectado por el fix."""
    fs._player_id_cache.clear()
    captured = {}

    def _fake_get(url, **kwargs):
        captured["url"] = url
        resp = MagicMock()
        resp.text = _jsonp([
            {"id": "P1", "type": "participants", "sport_id": 2, "url": "some-player"},
        ])
        return resp

    with patch("curl_cffi.requests.get", side_effect=_fake_get):
        result = fs.search_player_fs("Some Player")

    assert result == ("P1", "some-player")
    assert "sid=2" in captured["url"]
