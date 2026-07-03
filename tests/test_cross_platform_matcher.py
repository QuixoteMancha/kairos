from dataclasses import dataclass
from datetime import datetime, timedelta

from src.core.orchestration.cross_platform_matcher import (
    title_similarity,
    match_events,
    cross_platform_groups,
    extract_numbers,
    numbers_conflict,
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


def test_extract_numbers_handles_magnitude_and_percent_suffixes():
    assert extract_numbers("Will BTC hit $100k?") == [100000.0]
    assert extract_numbers("Fed cuts rates 25bps") == [25.0]
    assert extract_numbers("Inflation above 3.5%?") == [3.5]
    assert extract_numbers("No numbers here") == []


def test_numbers_conflict_detects_different_strikes():
    assert numbers_conflict("Will BTC hit $100k by Friday?", "Will BTC hit $110k by Friday?")
    assert not numbers_conflict("Will BTC hit $100k by Friday?", "BTC to reach $100,000 this Friday")
    # No numbers on one side -> not considered conflicting (ambiguous, not a clear mismatch)
    assert not numbers_conflict("Will BTC hit $100k?", "Will BTC rally this week?")


def test_match_events_does_not_group_similar_text_different_strike():
    base = datetime(2026, 7, 3, 18, 0)
    events = [
        _Ev("finance", "Will BTC be above $100k on Friday?", base, "kalshi"),
        _Ev("finance", "Will BTC be above $110k on Friday?", base, "polymarket"),
    ]
    groups = match_events(events)
    assert not any(g.is_cross_platform for g in groups)


def test_match_events_groups_same_strike_worded_differently():
    base = datetime(2026, 7, 3, 18, 0)
    events = [
        _Ev("finance", "Will BTC be above $100k on Friday?", base, "kalshi"),
        _Ev("finance", "BTC to close above $100,000 this Friday", base + timedelta(hours=1), "polymarket"),
    ]
    groups = match_events(events, title_threshold=0.6)
    cross = [g for g in groups if g.is_cross_platform]
    assert len(cross) == 1
    assert cross[0].platforms == {"kalshi", "polymarket"}


def test_match_events_handles_naive_and_aware_datetimes_without_crashing():
    """A naive resolution_date (no tzinfo) mixed with an aware one must not
    raise TypeError — it should be treated as UTC rather than crashing the
    whole matching pass over one malformed timestamp."""
    from datetime import timezone as _tz

    aware = datetime(2026, 7, 3, 18, 0, tzinfo=_tz.utc)
    naive = datetime(2026, 7, 3, 19, 0)  # no tzinfo — e.g. a client that failed to parse an offset
    events = [
        _Ev("politics", "Will candidate X win the special election?", aware, "kalshi"),
        _Ev("politics", "Will candidate X win the special election", naive, "polymarket"),
    ]
    groups = match_events(events)  # must not raise
    cross = [g for g in groups if g.is_cross_platform]
    assert len(cross) == 1


def test_match_events_does_not_transitively_chain_unrelated_events():
    """A~B similar, B~C similar, but A~C should NOT be — a naive anchor-only
    clustering would incorrectly put all three in one group. A and C mention
    clearly different strikes, so even though both are worded like B, they
    must not end up grouped together."""
    base = datetime(2026, 7, 3, 18, 0)
    a = _Ev("finance", "Will ETH be above $5k by Friday?", base, "kalshi")
    b = _Ev("finance", "Will ETH price move by Friday?", base, "manifold")  # vague, no numbers -> matches both
    c = _Ev("finance", "Will ETH be above $8k by Friday?", base, "polymarket")
    groups = match_events([a, b, c], title_threshold=0.5)

    # a and c must never land in the same group (different strikes, and
    # a~c text similarity/number-conflict would fail a direct comparison).
    for g in groups:
        platforms_in_group = {getattr(e, "platform") for e in g.events}
        if {"kalshi", "polymarket"} <= platforms_in_group:
            assert False, "a (kalshi/$5k) and c (polymarket/$8k) must not be grouped together"
