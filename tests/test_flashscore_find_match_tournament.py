"""Tests de `find_match_tournament` (2026-08-02, pedido explícito del
usuario: recuperar torneo real por nombre+fecha vía Flashscore para
partidos donde Kalshi no dio un `rules_secondary` parseable, típicamente
Grand Slams)."""

from __future__ import annotations

from unittest.mock import patch

from outer_apis.scrapers.tennis.flashscore_scraper import find_match_tournament


def _fake_results_html(*records_raw: str) -> str:
    return "~ZA÷ATP - SINGLES: Wimbledon (UK), grass¬ZEE÷x" + "".join(
        "~AA÷" + r for r in records_raw
    )


def _raw_record(match_id, ts, p1, p2, winner=1, sets1=2, sets2=0):
    return f"{match_id}¬AD÷{ts}¬AB÷3¬FH÷{p1}¬FK÷{p2}¬AS÷{winner}¬AG÷{sets1}¬AH÷{sets2}"


def test_finds_real_tournament_by_name_and_date():
    import time
    ts = int(time.mktime((2026, 7, 10, 12, 0, 0, 0, 0, 0)))
    html = _fake_results_html(_raw_record("m1", ts, "Sinner J.", "Djokovic N."))
    with patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.search_player",
        return_value=("pid1", "slug1"),
    ), patch(
        "outer_apis.scrapers.tennis.flashscore_scraper._fetch_results_page",
        return_value=html,
    ):
        tournament = find_match_tournament("Jannik Sinner", "Novak Djokovic", "2026-07-10")
    assert tournament == "Wimbledon"


def test_fail_closed_when_names_dont_match():
    import time
    ts = int(time.mktime((2026, 7, 10, 12, 0, 0, 0, 0, 0)))
    html = _fake_results_html(_raw_record("m1", ts, "Someone A.", "Someone B."))
    with patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.search_player",
        return_value=("pid1", "slug1"),
    ), patch(
        "outer_apis.scrapers.tennis.flashscore_scraper._fetch_results_page",
        return_value=html,
    ):
        assert find_match_tournament("Jannik Sinner", "Novak Djokovic", "2026-07-10") is None


def test_fail_closed_when_player_not_found():
    with patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.search_player", return_value=None,
    ):
        assert find_match_tournament("Nobody", "Nobody Else", "2026-07-10") is None


def test_falls_back_to_player_b_when_a_not_found():
    import time
    ts = int(time.mktime((2026, 7, 10, 12, 0, 0, 0, 0, 0)))
    html = _fake_results_html(_raw_record("m1", ts, "Sinner J.", "Djokovic N."))

    def _search(name):
        return ("pid2", "slug2") if name == "Novak Djokovic" else None

    with patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.search_player", side_effect=_search,
    ), patch(
        "outer_apis.scrapers.tennis.flashscore_scraper._fetch_results_page",
        return_value=html,
    ):
        tournament = find_match_tournament("Jannik Sinner", "Novak Djokovic", "2026-07-10")
    assert tournament == "Wimbledon"
