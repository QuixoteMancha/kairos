"""Tests del scraper de odds pre-partido reales de MLB
(SportsbookReviewsOnline, 2026-08-05, pedido explícito del usuario)."""

from __future__ import annotations

import io
from unittest.mock import patch

import pytest

from outer_apis.scrapers.baseball import sportsbookreviewsonline_scraper as scraper


def test_team_name_for_code_maps_known_codes():
    assert scraper.team_name_for_code("SEA") == "Seattle Mariners"
    assert scraper.team_name_for_code("nym") == "New York Mets"  # case-insensitive


def test_team_name_for_code_fail_closed_unknown():
    assert scraper.team_name_for_code("ZZZ") is None
    assert scraper.team_name_for_code(None) is None


def test_american_odds_to_implied_prob_favorite_and_underdog():
    assert round(scraper._american_odds_to_implied_prob(-160), 4) == round(160 / 260, 4)
    assert round(scraper._american_odds_to_implied_prob(140), 4) == round(100 / 240, 4)


def test_american_odds_to_implied_prob_fail_closed():
    assert scraper._american_odds_to_implied_prob(None) is None
    assert scraper._american_odds_to_implied_prob("bad") is None
    assert scraper._american_odds_to_implied_prob(0) is None


def test_mmdd_to_iso_parses_real_format():
    assert scraper._mmdd_to_iso(320, 2019) == "2019-03-20"
    assert scraper._mmdd_to_iso(1003, 2019) == "2019-10-03"


def test_mmdd_to_iso_fail_closed_invalid():
    assert scraper._mmdd_to_iso(1332, 2019) is None  # mes 13 no existe
    assert scraper._mmdd_to_iso(None, 2019) is None


def test_get_year_odds_out_of_range_returns_empty():
    assert scraper.get_year_odds(2005) == []
    assert scraper.get_year_odds(2030) == []


def _build_fake_workbook_bytes():
    pytest.importorskip("openpyxl")
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Date", "Rot", "VH", "Team", "Pitcher", "1st", "Open", "Close"])
    ws.append([320, 901, "V", "SEA", "X", 0, 120, 130])
    ws.append([320, 902, "H", "OAK", "Y", 1, -127, -140])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_get_year_odds_parses_real_workbook_shape():
    content = _build_fake_workbook_bytes()
    with patch.object(scraper, "_fetch_xlsx_bytes", return_value=content):
        odds = scraper.get_year_odds(2019)
    assert len(odds) == 1
    game = odds[0]
    assert game["date"] == "2019-03-20"
    assert game["away_team"] == "Seattle Mariners"
    assert game["home_team"] == "Oakland Athletics"
    assert game["home_moneyline_open"] == -127
    assert game["away_moneyline_close"] == 130


def test_get_year_odds_fetch_failure_fail_closed():
    with patch.object(scraper, "_fetch_xlsx_bytes", return_value=None):
        assert scraper.get_year_odds(2019) == []
