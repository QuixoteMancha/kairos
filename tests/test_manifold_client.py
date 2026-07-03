"""Tests for the Manifold Markets client (src/scrapers/manifold_client.py)."""

from datetime import datetime, timedelta, timezone

import pytest

from src.scrapers.manifold_client import (
    ManifoldClient,
    normalize_to_event,
    _hours_to_resolution,
    _is_sports,
    _matches_categories,
    MAX_HOURS_TO_RESOLUTION,
)


def _ms(hours_from_now: float) -> int:
    dt = datetime.now(timezone.utc) + timedelta(hours=hours_from_now)
    return int(dt.timestamp() * 1000)


def _market(**overrides) -> dict:
    base = {
        "id": "abc123",
        "question": "Will the Fed cut rates this week?",
        "outcomeType": "BINARY",
        "closeTime": _ms(24),
        "groupSlugs": ["economics"],
        "probability": 0.62,
        "isResolved": False,
        "url": "https://manifold.markets/x/abc123",
        "volume": 1000,
    }
    base.update(overrides)
    return base


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class TestNormalizeToEvent:
    def test_binary_market_maps_to_event(self):
        ev = normalize_to_event(_market())
        assert ev["platform"] == "manifold"
        assert ev["external_id"] == "manifold:abc123"
        assert ev["category"] == "economics"
        assert ev["status"] == "open"
        assert ev["title"] == "Will the Fed cut rates this week?"

    def test_resolved_market_status(self):
        ev = normalize_to_event(_market(isResolved=True))
        assert ev["status"] == "resolved"

    def test_multiple_choice_excluded(self):
        assert normalize_to_event(_market(outcomeType="MULTIPLE_CHOICE")) is None

    def test_free_response_excluded(self):
        assert normalize_to_event(_market(outcomeType="FREE_RESPONSE")) is None

    def test_missing_close_time_excluded(self):
        assert normalize_to_event(_market(closeTime=None)) is None

    def test_category_falls_back_when_only_sports_slugs(self):
        ev = normalize_to_event(_market(groupSlugs=["nba"]))
        assert ev["category"] == "prediction_market"


class TestHoursToResolution:
    def test_none_close_time(self):
        assert _hours_to_resolution(None) is None

    def test_future_close_time_positive(self):
        hrs = _hours_to_resolution(_ms(10))
        assert 9.9 < hrs < 10.1

    def test_past_close_time_negative(self):
        hrs = _hours_to_resolution(_ms(-5))
        assert hrs < 0


class TestIsSports:
    def test_sports_slug_detected(self):
        assert _is_sports(_market(groupSlugs=["nba"])) is True

    def test_non_sports_slug_not_detected(self):
        assert _is_sports(_market(groupSlugs=["economics"])) is False

    def test_question_heuristic_vs(self):
        assert _is_sports(_market(groupSlugs=[], question="Lakers vs Celtics winner?")) is True


class TestMatchesCategories:
    def test_no_filter_matches_all(self):
        assert _matches_categories(_market(), None) is True

    def test_filter_matches(self):
        assert _matches_categories(_market(groupSlugs=["economics"]), {"economics"}) is True

    def test_filter_excludes(self):
        assert _matches_categories(_market(groupSlugs=["economics"]), {"politics"}) is False


class TestManifoldClientGetFiniteEvents:
    def test_filters_by_resolution_window_and_sports(self, monkeypatch):
        client = ManifoldClient()

        page = [
            _market(id="a", closeTime=_ms(10), groupSlugs=["politics"]),   # keep
            _market(id="b", closeTime=_ms(72), groupSlugs=["economics"]),  # too far out
            _market(id="c", closeTime=_ms(5), groupSlugs=["nba"]),         # sports, excluded
            _market(id="d", closeTime=_ms(-1), groupSlugs=["politics"]),   # already closed
        ]

        calls = {"n": 0}

        def fake_get(url, params=None, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return FakeResponse(page)
            return FakeResponse([])  # stop pagination

        monkeypatch.setattr(client, "_get", fake_get)

        events = client.get_finite_events(max_hours=MAX_HOURS_TO_RESOLUTION)
        assert [e["external_id"] for e in events] == ["manifold:a"]

    def test_category_allowlist_applied(self, monkeypatch):
        client = ManifoldClient()
        page = [
            _market(id="a", closeTime=_ms(10), groupSlugs=["politics"]),
            _market(id="b", closeTime=_ms(10), groupSlugs=["economics"]),
        ]
        calls = {"n": 0}

        def fake_get(url, params=None, **kw):
            calls["n"] += 1
            return FakeResponse(page if calls["n"] == 1 else [])

        monkeypatch.setattr(client, "_get", fake_get)

        events = client.get_finite_events(categories={"economics"})
        assert [e["external_id"] for e in events] == ["manifold:b"]

    def test_pagination_stops_when_page_smaller_than_limit(self, monkeypatch):
        client = ManifoldClient()
        calls = {"n": 0}

        def fake_get(url, params=None, **kw):
            calls["n"] += 1
            return FakeResponse([_market(id=f"m{calls['n']}", closeTime=_ms(10))])

        monkeypatch.setattr(client, "_get", fake_get)

        events = client.get_finite_events(page_limit=500, max_pages=5)
        assert calls["n"] == 1
        assert len(events) == 1
