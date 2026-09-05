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


# Extracto real capturado en vivo 2026-09-04 (Noskova/Li, match_id
# `dWYHNcE6`, US Open): set 2 llega a 6-6 y el feed manda el resumen
# 'HC÷7¬HD÷8¬HE÷6¬HF÷6¬HK÷1' (sin HL, se sigue descartando) seguido del
# bloque 'HB÷Tiebreak - Set 2' con los puntos del propio tiebreak (sin
# HL tampoco -- ver docstring de `get_point_by_point`, bug real
# encontrado en vivo: esos puntos se perdían por completo). Termina con
# el arranque del set 3 para confirmar que `_flush_tiebreak` también
# dispara al ver el siguiente header 'HA÷Set 3', no solo al fin de feed.
_RAW_FEED_SET2_WITH_TIEBREAK = (
    "HA÷Set 2¬HB÷Point by point - Set 2¬~"
    "HC÷6¬HE÷6¬HG÷2¬HK÷2¬HL÷15:0, 15:15, 15:30, 30:30, 30:40¬~"
    "HC÷7¬HD÷8¬HE÷6¬HF÷6¬HK÷1¬~"
    "HB÷Tiebreak - Set 2¬~"
    "HC÷0¬HE÷1¬HG÷1¬HH÷1¬HK÷2¬~"
    "HC÷1¬HE÷1¬HG÷2¬HH÷2¬HK÷1¬~"
    "HC÷1¬HE÷2¬HG÷2¬HK÷2¬~"
    "HC÷2¬HE÷2¬HG÷1¬HK÷1¬~"
    "HC÷2¬HE÷3¬HG÷1¬HH÷1¬HK÷2¬~"
    "HC÷2¬HE÷4¬HG÷2¬HK÷2¬~"
    "HC÷2¬HE÷5¬HG÷2¬HK÷2¬~"
    "HC÷3¬HE÷5¬HG÷1¬HK÷1¬~"
    "HC÷4¬HE÷5¬HG÷1¬HK÷1¬~"
    "HC÷5¬HE÷5¬HG÷2¬HH÷2¬HK÷1¬~"
    "HC÷5¬HE÷6¬HG÷2¬HK÷2¬HM÷3,2¬~"
    "HC÷6¬HE÷6¬HG÷1¬HK÷1¬~"
    "HC÷7¬HE÷6¬HG÷1¬HK÷1¬HM÷2,1¬~"
    "HC÷8¬HE÷6¬HG÷2¬HH÷2¬HK÷1¬~"
    "HA÷Set 3¬HB÷Point by point - Set 3¬~"
    "HC÷0¬HE÷1¬HG÷2¬HK÷2¬HL÷15:0, 15:15, 15:30, 30:30, 30:40¬~"
    "A1÷¬~"
)


def test_get_point_by_point_captures_tiebreak_final_result():
    with patch(
        "requests.get", return_value=_fake_response(text=_RAW_FEED_SET2_WITH_TIEBREAK),
    ), patch("outer_apis.scrapers.tennis.flashscore_scraper.flashscore_throttle.wait"):
        result = get_point_by_point("dWYHNcE6")

    assert result is not None
    # el fixture arranca directo en "Set 2" -- las keys del dict son por
    # orden de aparición ("set_1", "set_2"), no por el número real del
    # set en el label, mismo criterio que el resto de este parser.
    set2_games = result["set_1"]["games"]
    # 1 juego regular (6-6) + 1 juego sintético para el tiebreak completo.
    assert len(set2_games) == 2
    tb_game = set2_games[-1]
    assert tb_game["tiebreak"] is True
    # tiebreak real 8-6, ganado por el lado 1 (set final 7-6 para lado 1).
    assert tb_game["winner_side"] == 1
    assert tb_game["points"][0] == "0:1"
    assert tb_game["points"][-1] == "8:6"
    assert tb_game["raw"] == {"HC": "8", "HE": "6", "HG": "2", "HH": "2"}

    # el arranque del set 3 no quedó contaminado por el tiebreak previo.
    set3_games = result["set_2"]["games"]
    assert len(set3_games) == 1
    assert "tiebreak" not in set3_games[0]


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
