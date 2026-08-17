"""Contrato del feed crudo real de Flashscore, sin requests de red."""

from pathlib import Path
from unittest.mock import patch

from database.historical.tennis.matchstats import (
    classify_unusable_stats, stats_are_usable,
)
from outer_apis.scrapers.tennis.flashscore_scraper import get_match_statistics


_FIXTURES = Path(__file__).parent / "fixtures"


class _Response:
    status_code = 200

    def __init__(self, text: str):
        self.text = text


def _parse_fixture(name: str):
    response = _Response((_FIXTURES / name).read_text(encoding="utf-8").strip())
    with patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.flashscore_throttle.wait",
    ), patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.brightdata_proxy.fetch_with_ip_block_fallback",
        return_value=response,
    ):
        return get_match_statistics("fixture")


def test_full_feed_remains_parseable_and_usable():
    stats = _parse_fixture("flashscore_stats_full.txt")
    assert stats["match"]["Aces"] == ("2", "8")
    assert stats["set_1"]["Aces"] == ("1", "3")
    assert stats_are_usable(stats) is True


def test_reduced_feed_is_coverage_gap_not_parser_failure():
    stats = _parse_fixture("flashscore_stats_reduced.txt")
    assert stats_are_usable(stats) is False
    assert classify_unusable_stats(stats) == "source_incomplete"
