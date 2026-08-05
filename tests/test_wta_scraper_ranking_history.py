"""Tests de las nuevas funciones de ranking histórico real de
`wta_scraper.py` (2026-08-05, pedido explícito del usuario)."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

from outer_apis.scrapers.tennis import wta_scraper


def _fake_resp(json_data, status=200):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = json_data
    return m


def test_get_ranked_players_at_extracts_id_name_ranking():
    page1 = [
        {"player": {"id": 1, "fullName": "A"}, "ranking": 1},
        {"player": {"id": 2, "fullName": "B"}, "ranking": 2},
    ]
    with patch.object(wta_scraper, "_get", return_value=_fake_resp(page1)):
        result = wta_scraper.get_ranked_players_at("2020-01-01", n=2)
    assert result == [{"id": 1, "name": "A", "ranking": 1}, {"id": 2, "name": "B", "ranking": 2}]


def test_get_ranked_players_at_fail_closed_on_network_error():
    with patch.object(wta_scraper, "_get", return_value=None):
        assert wta_scraper.get_ranked_players_at("2020-01-01") == []


def test_get_weekly_ranking_history_extracts_real_series():
    data = {"weeklyRankings": [
        {"rankedAt": "2020-01-01T00:00:00Z", "singlesRanking": 50},
        {"rankedAt": "2019-01-01T00:00:00Z", "singlesRanking": 100},
    ]}
    with patch.object(wta_scraper, "_get", return_value=_fake_resp(data)):
        result = wta_scraper.get_weekly_ranking_history(123)
    assert result == [
        {"ranked_at": "2019-01-01", "singles_ranking": 100},
        {"ranked_at": "2020-01-01", "singles_ranking": 50},
    ]


def test_get_weekly_ranking_history_skips_incomplete_entries():
    data = {"weeklyRankings": [{"rankedAt": None, "singlesRanking": 50}, {"rankedAt": "2020-01-01T00:00:00Z", "singlesRanking": None}]}
    with patch.object(wta_scraper, "_get", return_value=_fake_resp(data)):
        assert wta_scraper.get_weekly_ranking_history(123) == []


def test_get_weekly_ranking_history_fail_closed_on_network_error():
    with patch.object(wta_scraper, "_get", return_value=None):
        assert wta_scraper.get_weekly_ranking_history(123) == []
