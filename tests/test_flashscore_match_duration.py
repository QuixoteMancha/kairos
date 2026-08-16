"""Tests de `get_match_duration` (flashscore_scraper.py) -- 2026-08-12,
pedido explícito del usuario ("necesitamos la duración de los matches").
Feed `df_su_1`, verificado en vivo contra un partido real (Rybakina/
Samsonova, match_id "QivlICam") antes de escribir estos tests. Mockea
toda request HTTP real."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

from outer_apis.scrapers.tennis.flashscore_scraper import get_match_duration, _parse_hmm_to_minutes


def _fake_response(status_code=200, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


# Feed real capturado en vivo (Rybakina/Samsonova, 3 sets).
_REAL_FEED_3_SETS = "AC÷3¬BA÷4¬BB÷6¬RC÷0:59¬~BC÷6¬BD÷4¬RD÷0:53¬~BE÷4¬BF÷6¬RE÷1:03¬~RB÷2:55¬~PSPA÷2¬A1÷¬~"


def test_parse_hmm_to_minutes_real_values():
    assert _parse_hmm_to_minutes("2:55") == 175
    assert _parse_hmm_to_minutes("0:59") == 59
    assert _parse_hmm_to_minutes("0:53") == 53


def test_parse_hmm_to_minutes_fail_closed_on_bad_format():
    assert _parse_hmm_to_minutes(None) is None
    assert _parse_hmm_to_minutes("") is None
    assert _parse_hmm_to_minutes("garbage") is None
    assert _parse_hmm_to_minutes("2:5") is None  # minutos deben ser 2 dígitos


def test_get_match_duration_parses_real_feed():
    with patch(
        "requests.get", return_value=_fake_response(text=_REAL_FEED_3_SETS),
    ), patch("outer_apis.scrapers.tennis.flashscore_scraper.flashscore_throttle.wait"):
        result = get_match_duration("QivlICam")

    assert result == {
        "total_minutes": 175,
        "set_1_minutes": 59, "set_2_minutes": 53, "set_3_minutes": 63,
        "set_4_minutes": None, "set_5_minutes": None,
    }
    # invariante real: la suma de sets coincide con el total.
    assert result["set_1_minutes"] + result["set_2_minutes"] + result["set_3_minutes"] == result["total_minutes"]


def test_get_match_duration_5_sets():
    feed = (
        "AC÷5¬BA÷6¬BB÷4¬RC÷0:45¬~BC÷4¬BD÷6¬RD÷0:50¬~BE÷6¬BF÷3¬RE÷0:40¬~"
        "BG÷4¬BH÷6¬RF÷0:55¬~BI÷7¬BJ÷5¬RG÷1:10¬~RB÷4:20¬~A1÷¬~"
    )
    with patch(
        "requests.get", return_value=_fake_response(text=feed),
    ), patch("outer_apis.scrapers.tennis.flashscore_scraper.flashscore_throttle.wait"):
        result = get_match_duration("some_5set_match")

    assert result["set_5_minutes"] == 70
    assert result["total_minutes"] == 260


def test_get_match_duration_fail_closed_on_http_error():
    with patch(
        "requests.get", return_value=_fake_response(status_code=404, text=""),
    ), patch("outer_apis.scrapers.tennis.flashscore_scraper.flashscore_throttle.wait"):
        assert get_match_duration("bad_id") is None


def test_get_match_duration_fail_closed_on_empty_feed():
    with patch(
        "requests.get", return_value=_fake_response(text="0"),
    ), patch("outer_apis.scrapers.tennis.flashscore_scraper.flashscore_throttle.wait"):
        assert get_match_duration("no_data") is None


def test_get_match_duration_fail_closed_when_no_duration_fields_present():
    with patch(
        "requests.get", return_value=_fake_response(text="AC÷3¬BA÷4¬BB÷6¬~"),
    ), patch("outer_apis.scrapers.tennis.flashscore_scraper.flashscore_throttle.wait"):
        assert get_match_duration("weird_feed") is None


def test_get_match_duration_fail_closed_on_request_exception():
    with patch(
        "requests.get", side_effect=Exception("network down"),
    ), patch("outer_apis.scrapers.tennis.flashscore_scraper.flashscore_throttle.wait"):
        assert get_match_duration("x") is None
