"""Regression tests for 2 real bugs found+fixed 2026-07-07, the first time
Sofascore's API was reachable again since the 2026-06-22 challenge/proxy
outage (BrightData IP-whitelist was never auto-retried -- see
brightdata_utils.request_via_proxy_with_ip_retry and its wiring into
SofascoreScraper._get / flashscore_scraper.py).

Both bugs were structurally invisible until tested against LIVE data (the
API was blocked the whole time these code paths existed), and both are
silent-wrong-data bugs, not exceptions -- exactly the kind this project's
fail-closed discipline is meant to catch, confirmed here via mocked HTTP
matching the REAL response shapes captured live on 2026-07-07."""
from __future__ import annotations

from unittest.mock import patch

from src.scrapers.sofascore.client import SofascoreScraper


def _mock_get(monkeypatch, path_to_json: dict):
    def fake_get(self, path, params=None):
        return path_to_json.get(path)
    monkeypatch.setattr(SofascoreScraper, "_get", fake_get)


def test_search_player_id_accepts_real_team_typed_individual_athlete(monkeypatch):
    """Bug #1: Sofascore's /search/all wraps individual tennis players under
    top-level item["type"] == "team" (confirmed live), not "player" as the
    code previously assumed -- the old filter silently discarded every real
    result forever."""
    real_shape = {
        "results": [
            {
                "type": "team",  # NOT "player" -- the real observed shape
                "score": 123.0,
                "entity": {
                    "id": 206570,
                    "name": "Jannik Sinner",
                    "type": 1,
                    "sport": {"id": 5, "slug": "tennis", "name": "Tennis"},
                },
            },
            {
                "type": "team",
                "score": 10.0,
                "entity": {
                    "id": 999999,
                    "name": "Some Football Team",
                    "type": 0,
                    "sport": {"id": 1, "slug": "football", "name": "Football"},
                },
            },
        ]
    }

    def fake_get(self, path, params=None):
        assert path == "/search/all"
        assert params == {"q": "Jannik Sinner"}
        return real_shape

    monkeypatch.setattr(SofascoreScraper, "_get", fake_get)
    c = SofascoreScraper()
    pid = c.search_player_id("Jannik Sinner")
    assert pid == 206570  # NOT None, and NOT the football team


def test_search_player_id_returns_none_when_no_tennis_match(monkeypatch):
    real_shape = {
        "results": [
            {"type": "team", "entity": {"id": 1, "name": "Someone Else",
                                          "sport": {"slug": "basketball"}}},
        ]
    }
    monkeypatch.setattr(SofascoreScraper, "_get", lambda self, path, params=None: real_shape)
    c = SofascoreScraper()
    assert c.search_player_id("Jannik Sinner") is None


def test_get_player_last_matches_uses_team_namespace_not_player(monkeypatch):
    """Bug #2: the id returned by search_player_id() for an individual
    tennis player lives in Sofascore's /team/ namespace, NOT /player/ --
    confirmed live: /player/206570 resolved to an unrelated Croatian
    football player, while /team/206570 correctly resolved to Jannik
    Sinner. Calling /player/{id} would silently return someone else's match
    history instead of erroring."""
    calls = []

    def fake_get(self, path, params=None):
        calls.append(path)
        if path == "/team/206570/events/last/0":
            return {
                "events": [{
                    "id": 15708827,
                    "homeTeam": {"id": 406728, "name": "Someone"},
                    "awayTeam": {"id": 206570, "name": "Jannik Sinner"},
                    "homeScore": {"current": 2}, "awayScore": {"current": 0},
                    "startTimestamp": 1774483200,
                    "tournament": {"category": {"name": "ATP", "sport": {"slug": "tennis"}}},
                    "groundType": "Hardcourt outdoor",
                    "periods": {},
                }],
                "hasNextPage": False,
            }
        if path.startswith("/player/"):
            raise AssertionError(f"Must never call /player/ namespace for a searched team-id: {path}")
        return None

    monkeypatch.setattr(SofascoreScraper, "_get", fake_get)
    c = SofascoreScraper()
    matches = c.get_player_last_matches(206570, n=5, categories=None)
    assert any("/team/206570/events/last/" in p for p in calls)
    assert not any(p.startswith("/player/") for p in calls)
