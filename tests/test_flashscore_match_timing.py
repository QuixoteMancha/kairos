"""Tests de `get_match_timing` (flashscore_scraper.py) -- 2026-09-05,
pedido explícito del usuario ("investiga si Flashscore expone
timestamps de inicio/fin real" -> "construye get_match_timing()").
El bloque `_RAW_BLOCK_*` es un extracto REAL capturado en vivo del feed
diario `f_2_{day}_3_en_1` para el partido Cobolli/Blockx (match_id
`0Ka56MAk`, hoy 2026-09-05), verificado contra `get_match_duration`
antes de escribirse acá: `(AO-AD)/60 = 163.9` vs `total_minutes = 165`
(diferencia de redondeo esperada, no un bug)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import outer_apis.scrapers.tennis.flashscore_scraper as flashscore_scraper
from outer_apis.scrapers.tennis.flashscore_scraper import get_match_timing


@pytest.fixture(autouse=True)
def _clear_daily_feed_cache():
    # `_fetch_daily_matches` caches per day_offset for 120s -- sin esto,
    # el texto mockeado de un test contaminaría el siguiente.
    flashscore_scraper._daily_feed_cache.clear()
    yield
    flashscore_scraper._daily_feed_cache.clear()


def _fake_response(status_code=200, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


_FINISHED_BLOCK = (
    "0Ka56MAk¬AD÷1788628200¬ADE÷1788628200¬AB÷3¬CR÷3¬AC÷3¬CX÷Blockx A.¬"
    "AO÷1788638037¬AE÷Blockx A.¬FH÷Blockx A.¬AS÷1¬AF÷Cobolli F.¬FK÷Cobolli F.¬AW÷1¬"
)
_SCHEDULED_BLOCK = "abc123¬AD÷1788628200¬AB÷1¬AE÷Player A¬AF÷Player B¬"
_MISSING_START_BLOCK = "xyz999¬AB÷3¬AO÷1788638037¬AE÷Player A¬AF÷Player B¬"

_HEADER = "some_header_block"


def _daily_feed(block: str) -> str:
    return f"~AA÷{_HEADER}~AA÷{block}"


def test_get_match_timing_parses_real_finished_match():
    with patch(
        "requests.get", return_value=_fake_response(text=_daily_feed(_FINISHED_BLOCK)),
    ), patch("outer_apis.scrapers.tennis.flashscore_scraper.flashscore_throttle.wait"):
        result = get_match_timing("0Ka56MAk")

    assert result == {
        "start_at": "2026-09-05T17:10:00+00:00",
        "end_at": "2026-09-05T19:53:57+00:00",
        "duration_minutes": 164,
    }


def test_get_match_timing_fail_closed_when_not_finished():
    with patch(
        "requests.get", return_value=_fake_response(text=_daily_feed(_SCHEDULED_BLOCK)),
    ), patch("outer_apis.scrapers.tennis.flashscore_scraper.flashscore_throttle.wait"):
        assert get_match_timing("abc123") is None


def test_get_match_timing_fail_closed_when_start_missing():
    with patch(
        "requests.get", return_value=_fake_response(text=_daily_feed(_MISSING_START_BLOCK)),
    ), patch("outer_apis.scrapers.tennis.flashscore_scraper.flashscore_throttle.wait"):
        assert get_match_timing("xyz999") is None


def test_get_match_timing_fail_closed_when_match_id_not_found():
    with patch(
        "requests.get", return_value=_fake_response(text=_daily_feed(_FINISHED_BLOCK)),
    ), patch("outer_apis.scrapers.tennis.flashscore_scraper.flashscore_throttle.wait"):
        assert get_match_timing("does-not-exist") is None


def test_get_match_timing_fail_closed_on_http_error():
    with patch(
        "requests.get", return_value=_fake_response(status_code=404, text=""),
    ), patch("outer_apis.scrapers.tennis.flashscore_scraper.flashscore_throttle.wait"):
        assert get_match_timing("0Ka56MAk") is None
