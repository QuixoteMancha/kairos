"""Tests de `get_point_by_point` (flashscore_scraper.py) -- 2026-08-10,
pedido explícito del usuario ("amplía el scraper de flashscore para
tener el punto a punto y los quiebres"). El feed real usado como fixture
(`_RAW_FEED_SET1_SAMPLE`) es un extracto REAL capturado en vivo del
partido Rybakina/Samsonova (match_id `QivlICam`, feed `df_mh_1`),
verificado ya contra el marcador real de sets (4-6) antes de escribirse
acá. Mockea toda request HTTP real."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

from outer_apis.scrapers.tennis.flashscore_scraper import get_point_by_point


def _fake_response(status_code=200, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


# Extracto real del Set 1 completo (4 juegos ganados por lado 1, 6 por
# lado 2 -- coincide con el marcador real 4-6 de ese set) más el cierre
# de feed `A1÷`.
_RAW_FEED_SET1_ONLY = (
    "HA÷Set 1¬HB÷Point by point - Set 1¬~"
    "HC÷1¬HE÷0¬HG÷2¬HH÷2¬HK÷1¬HL÷0:15, 15:15, 30:15, 40:15 |B1|¬~"
    "HC÷1¬HE÷1¬HG÷1¬HH÷1¬HK÷2¬HL÷0:15, 15:15, 30:15, 30:30, 30:40 |B1|, 40:40, 40:A |B1|, 40:40, A:40, 40:40, 40:A |B1|¬~"
    "HC÷1¬HE÷2¬HG÷2¬HK÷2¬HL÷15:0, 15:15, 30:15, 40:15 |B1|, 40:30 |B1|, 40:40, 40:A¬~"
    "HC÷2¬HE÷2¬HG÷1¬HK÷1¬HL÷15:0, 30:0, 30:15, 30:30, 40:30¬~"
    "HC÷3¬HE÷2¬HG÷2¬HH÷2¬HK÷1¬HL÷0:15, 15:15, 15:30, 30:30, 40:30 |B1|, 40:40, A:40 |B1|¬~"
    "HC÷3¬HE÷3¬HG÷1¬HH÷1¬HK÷2¬HL÷0:15, 15:15, 15:30, 30:30, 30:40 |B1|, 40:40, A:40, 40:40, 40:A |B1|¬~"
    "HC÷4¬HE÷3¬HG÷2¬HH÷2¬HK÷1¬HL÷15:0, 30:0, 40:0 |B1|, 40:15 |B1|, 40:30 |B1|¬~"
    "HC÷4¬HE÷4¬HG÷1¬HH÷1¬HK÷2¬HL÷15:0, 15:15, 30:15, 40:15, 40:30, 40:40, 40:A |B1|, 40:40, A:40, 40:40, 40:A |B1|, 40:40, 40:A |B1|¬~"
    "HC÷4¬HE÷5¬HG÷2¬HK÷2¬HL÷0:15, 0:30, 0:40¬~"
    "HC÷4¬HE÷6¬HG÷1¬HH÷1¬HK÷2¬HL÷15:0, 30:0, 40:0, 40:15, 40:30, 40:40, 40:A |B1| |B2|¬~"
    "A1÷¬~"
)


def test_get_point_by_point_parses_real_feed_sample():
    with patch(
        "requests.get", return_value=_fake_response(text=_RAW_FEED_SET1_ONLY),
    ), patch("outer_apis.scrapers.tennis.flashscore_scraper.flashscore_throttle.wait"):
        result = get_point_by_point("QivlICam")

    assert result is not None
    assert list(result.keys()) == ["set_1"]
    games = result["set_1"]["games"]
    assert result["set_1"]["label"] == "Point by point - Set 1"
    assert len(games) == 10
    # marcador real del set (4-6) reconstruido sumando winner_side
    assert sum(1 for g in games if g["winner_side"] == 1) == 4
    assert sum(1 for g in games if g["winner_side"] == 2) == 6


def test_get_point_by_point_extracts_break_points_with_prior_sequence():
    with patch(
        "requests.get", return_value=_fake_response(text=_RAW_FEED_SET1_ONLY),
    ), patch("outer_apis.scrapers.tennis.flashscore_scraper.flashscore_throttle.wait"):
        result = get_point_by_point("QivlICam")

    first_game = result["set_1"]["games"][0]
    assert first_game["points"] == ["0:15", "15:15", "30:15", "40:15 |B1|"]
    assert first_game["break_points"] == [
        {"point_index": 3, "sequence_raw": "40:15 |B1|"},
    ]
    # último juego del set: doble marcador |B1| |B2| en el mismo punto
    last_game = result["set_1"]["games"][-1]
    assert last_game["break_points"][-1]["sequence_raw"] == "40:A |B1| |B2|"


def test_get_point_by_point_fail_closed_on_http_error():
    with patch(
        "requests.get", return_value=_fake_response(status_code=404, text=""),
    ), patch("outer_apis.scrapers.tennis.flashscore_scraper.flashscore_throttle.wait"):
        assert get_point_by_point("bad_id") is None


def test_get_point_by_point_fail_closed_on_empty_feed():
    with patch(
        "requests.get", return_value=_fake_response(text="0"),
    ), patch("outer_apis.scrapers.tennis.flashscore_scraper.flashscore_throttle.wait"):
        assert get_point_by_point("no_data") is None


def test_get_point_by_point_fail_closed_on_request_exception():
    with patch(
        "requests.get", side_effect=Exception("network down"),
    ), patch("outer_apis.scrapers.tennis.flashscore_scraper.flashscore_throttle.wait"):
        assert get_point_by_point("x") is None
