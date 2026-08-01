"""Tests de estadísticas reales de partido vía Flashscore (2026-08-01,
pedido explícito del usuario: "cuando se detecte que un partido terminó
se debe hacer fetch de las stats... para identificar el match usar
coincidencia de nombres + fecha + torneo"). Alcance: SOLO tenis."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis.tennismatchstats import TennisMatchStats
from database.historical.tennis.matchstats import has_match_stats, save_match_stats, get_match_stats
from outer_apis.scrapers.tennis.flashscore_scraper import (
    _extract_tournament_name, get_match_statistics, find_match_id, _parse_record,
)


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        TennisMatchStats.delete().execute()
    yield
    with db_historical.connection_context():
        TennisMatchStats.delete().execute()


# ── database/historical/tennis/matchstats.py ────────────────────────────

def test_save_and_get_match_stats_roundtrip():
    stats = {"match": {"Aces": ["3", "0"], "Double Faults": ["5", "2"]}}
    save_match_stats("KXATPMATCH-X", "fs123", stats)
    assert has_match_stats("KXATPMATCH-X") is True
    assert get_match_stats("KXATPMATCH-X") == stats


def test_save_match_stats_skips_empty():
    save_match_stats("KXATPMATCH-EMPTY", "fs999", {})
    assert has_match_stats("KXATPMATCH-EMPTY") is False


def test_get_match_stats_none_when_missing():
    assert get_match_stats("KXATPMATCH-NOPE") is None


def test_has_match_stats_false_on_db_failure():
    with patch(
        "database.historical.tennis.matchstats.TennisMatchStats.select",
        side_effect=Exception("boom"),
    ):
        assert has_match_stats("anything") is False


# ── flashscore_scraper: extracción de torneo ────────────────────────────

def test_extract_tournament_name_with_country():
    za = "ATP - SINGLES: Barcelona (Spain), clay¬ZEE÷rZvXbpeD¬ZB÷3473162"
    assert _extract_tournament_name(za) == "Barcelona"


def test_extract_tournament_name_without_country():
    za = "WTA - SINGLES: Washington, hard¬ZEE÷abc"
    assert _extract_tournament_name(za) == "Washington"


def test_extract_tournament_name_fail_closed_on_unrecognized_format():
    assert _extract_tournament_name("garbage without colon") is None


# ── flashscore_scraper: get_match_statistics (parser del feed real) ────

_REAL_STATS_FEED = (
    "SE÷Match¬~SF÷Service¬~SG÷Aces¬SH÷3¬SI÷0¬~SG÷Double Faults¬SH÷5¬SI÷2¬~"
    "SG÷1st serve percentage¬SH÷58%¬SI÷51%¬~SF÷Points¬~SG÷Winners¬SH÷20¬SI÷13¬~"
    "SE÷Set 1¬~SF÷Service¬~SG÷Aces¬SH÷1¬SI÷0¬~A1÷¬~"
)


def _make_response(text, status=200):
    class _FakeResp:
        pass
    r = _FakeResp()
    r.status_code = status
    r.text = text
    return r


def test_get_match_statistics_parses_sections_and_values():
    with patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.brightdata_proxy.request_via_proxy_with_ip_retry",
        return_value=_make_response(_REAL_STATS_FEED),
    ):
        stats = get_match_statistics("fNdO7zfc")

    assert stats["match"]["Aces"] == ("3", "0")
    assert stats["match"]["Double Faults"] == ("5", "2")
    assert stats["match"]["1st serve percentage"] == ("58%", "51%")
    assert stats["match"]["Winners"] == ("20", "13")
    assert stats["set_1"]["Aces"] == ("1", "0")


def test_get_match_statistics_fail_closed_on_empty_response():
    with patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.brightdata_proxy.request_via_proxy_with_ip_retry",
        return_value=_make_response("0"),
    ):
        assert get_match_statistics("nostats") is None


def test_get_match_statistics_fail_closed_on_request_failure():
    with patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.brightdata_proxy.request_via_proxy_with_ip_retry",
        return_value=None,
    ):
        assert get_match_statistics("whatever") is None


def test_get_match_statistics_fail_closed_on_http_error():
    with patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.brightdata_proxy.request_via_proxy_with_ip_retry",
        return_value=_make_response("", status=500),
    ):
        assert get_match_statistics("whatever") is None


# ── flashscore_scraper: find_match_id ───────────────────────────────────

def _fake_results_html(*records_raw: str) -> str:
    """Simula el shape mínimo que `_parse_all_records_with_tournament`
    espera: un bloque ~ZA÷ seguido de records ~AA÷."""
    return "~ZA÷ATP - SINGLES: Montreal (Canada), hard¬ZEE÷x" + "".join(
        "~AA÷" + r for r in records_raw
    )


def _raw_record(match_id, ts, p1, p2, winner=1, sets1=2, sets2=0):
    return (
        f"{match_id}¬AD÷{ts}¬AB÷3¬FH÷{p1}¬FK÷{p2}¬AS÷{winner}¬AG÷{sets1}¬AH÷{sets2}"
    )


def test_find_match_id_matches_both_names_and_date():
    import time
    ts = int(time.mktime((2026, 8, 1, 12, 0, 0, 0, 0, 0)))
    html = _fake_results_html(
        _raw_record("abc123", ts, "Medjedovic H.", "Cerundolo J.M."),
    )
    with patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.search_player",
        return_value=("pid1", "slug1"),
    ), patch(
        "outer_apis.scrapers.tennis.flashscore_scraper._fetch_results_page",
        return_value=html,
    ):
        match_id = find_match_id(
            "Hamad Medjedovic", "Juan Manuel Cerundolo", "2026-08-01", "Montreal",
        )
    assert match_id == "abc123"


def test_find_match_id_fail_closed_when_names_dont_match():
    import time
    ts = int(time.mktime((2026, 8, 1, 12, 0, 0, 0, 0, 0)))
    html = _fake_results_html(
        _raw_record("abc123", ts, "Someone Else A.", "Someone Else B."),
    )
    with patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.search_player",
        return_value=("pid1", "slug1"),
    ), patch(
        "outer_apis.scrapers.tennis.flashscore_scraper._fetch_results_page",
        return_value=html,
    ):
        match_id = find_match_id(
            "Hamad Medjedovic", "Juan Manuel Cerundolo", "2026-08-01", "Montreal",
        )
    assert match_id is None


def test_find_match_id_fail_closed_when_date_too_far():
    import time
    ts = int(time.mktime((2026, 1, 1, 12, 0, 0, 0, 0, 0)))  # muy lejos de la fecha buscada
    html = _fake_results_html(
        _raw_record("abc123", ts, "Medjedovic H.", "Cerundolo J.M."),
    )
    with patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.search_player",
        return_value=("pid1", "slug1"),
    ), patch(
        "outer_apis.scrapers.tennis.flashscore_scraper._fetch_results_page",
        return_value=html,
    ):
        match_id = find_match_id(
            "Hamad Medjedovic", "Juan Manuel Cerundolo", "2026-08-01", "Montreal",
        )
    assert match_id is None


def test_find_match_id_fail_closed_when_player_not_found():
    with patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.search_player",
        return_value=None,
    ):
        assert find_match_id("Nobody", "Nobody Else", "2026-08-01") is None


def test_find_match_id_falls_back_to_player_b_when_a_not_found():
    import time
    ts = int(time.mktime((2026, 8, 1, 12, 0, 0, 0, 0, 0)))
    html = _fake_results_html(
        _raw_record("abc123", ts, "Medjedovic H.", "Cerundolo J.M."),
    )

    def _search(name):
        return ("pid2", "slug2") if name == "Juan Manuel Cerundolo" else None

    with patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.search_player", side_effect=_search,
    ), patch(
        "outer_apis.scrapers.tennis.flashscore_scraper._fetch_results_page",
        return_value=html,
    ):
        match_id = find_match_id(
            "Hamad Medjedovic", "Juan Manuel Cerundolo", "2026-08-01",
        )
    assert match_id == "abc123"
