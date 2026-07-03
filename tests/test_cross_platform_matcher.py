from dataclasses import dataclass
from datetime import datetime, timedelta

from src.core.orchestration.cross_platform_matcher import (
    title_similarity,
    match_events,
    cross_platform_groups,
)


@dataclass
class _Ev:
    category: str
    title: str
    resolution_date: datetime | None
    platform: str


def test_title_similarity_identical():
    assert title_similarity("Will BTC hit $100k?", "will btc hit $100k?") == 1.0


def test_title_similarity_dissimilar():
    assert title_similarity("Will BTC hit $100k?", "Fed rate decision June") < 0.5


def test_match_events_groups_same_event_across_platforms():
    base = datetime(2026, 7, 3, 18, 0)
    events = [
        _Ev("finance", "Will the Fed cut rates by 25bps in July?", base, "kalshi"),
        _Ev("finance", "Fed cuts rates 25bps in July", base + timedelta(hours=1), "polymarket"),
        _Ev("finance", "Fed cuts rates 25 bps in July meeting", base + timedelta(hours=2), "manifold"),
        _Ev("sports", "Lakers beat Celtics tonight?", base, "kalshi"),
    ]
    groups = match_events(events, title_threshold=0.6)
    cross = [g for g in groups if g.is_cross_platform]
    assert len(cross) == 1
    assert cross[0].platforms == {"kalshi", "polymarket", "manifold"}


def test_match_events_respects_category_and_window():
    base = datetime(2026, 7, 3, 18, 0)
    events = [
        _Ev("finance", "Fed cuts rates 25bps", base, "kalshi"),
        _Ev("finance", "Fed cuts rates 25bps", base + timedelta(hours=12), "manifold"),
    ]
    groups = match_events(events)
    assert not any(g.is_cross_platform for g in groups)


def test_cross_platform_groups_excludes_single_platform():
    base = datetime(2026, 7, 3, 18, 0)
    events = [
        _Ev("politics", "Will candidate X win the primary?", base, "kalshi"),
    ]
    assert cross_platform_groups(events) == []
