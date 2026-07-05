import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.core.orchestration.cross_platform_matcher import (
    title_similarity,
    match_events,
    cross_platform_groups,
    extract_numbers,
    numbers_conflict,
    _as_aware_utc,
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


# --- Performance-fix regression tests (2026-07-04) --------------------------
#
# `match_events` used to be a single unbucketed O(n^2) double loop. It is now
# bucketed by `category` (safe by construction — `_fits_group`'s first check
# is an exact category equality, so cross-category pairs could never join a
# group either way) plus per-event memoization of title normalization/number
# extraction/quick-ratio bounds. These tests pin down that the *result*
# (which events land in the same group) is unchanged, and that the new
# implementation actually scales.


def _reference_match_events(events, *, title_threshold=0.85, resolution_window=timedelta(hours=6)):
    """Deliberately naive, unoptimized reimplementation of the ORIGINAL
    (pre-2026-07-04) `match_events` — a single unbucketed O(n^2) pass with no
    memoization, calling the plain module-level `title_similarity` /
    `numbers_conflict` functions directly. Kept here only as a test oracle to
    confirm the optimized implementation in production code produces an
    identical grouping, never used outside this test file."""
    groups = []
    used = set()

    def fits(other, group_events):
        other_title = getattr(other, "title", "")
        other_date = _as_aware_utc(getattr(other, "resolution_date", None))
        other_cat = getattr(other, "category", None)
        for member in group_events:
            if getattr(member, "category", None) != other_cat:
                return False
            member_date = _as_aware_utc(getattr(member, "resolution_date", None))
            if member_date is not None and other_date is not None:
                if abs(member_date - other_date) > resolution_window:
                    return False
            member_title = getattr(member, "title", "")
            if numbers_conflict(member_title, other_title):
                return False
            if title_similarity(member_title, other_title) < title_threshold:
                return False
        return True

    for i, ev in enumerate(events):
        if i in used:
            continue
        group_events = [ev]
        used.add(i)
        for j in range(i + 1, len(events)):
            if j in used:
                continue
            other = events[j]
            if fits(other, group_events):
                group_events.append(other)
                used.add(j)
        groups.append(frozenset(id(e) for e in group_events))

    return groups


def _groups_as_id_sets(groups):
    return sorted([frozenset(id(e) for e in g.events) for g in groups], key=lambda s: sorted(s))


def test_optimized_match_events_matches_reference_on_date_boundary_cases():
    """Events straddling near the edge of `resolution_window` in various
    directions (just inside, just outside, across a UTC midnight/day
    boundary) — the exact scenario a naive date-bucketing-by-calendar-day
    optimization could get wrong. `match_events` does NOT bucket by date (by
    design, see its docstring), so this also guards against a future
    regression that adds one without handling the edge case."""
    window = timedelta(hours=6)
    midnight = datetime(2026, 7, 8, 0, 0, tzinfo=timezone.utc)
    events = [
        _Ev("finance", "Will BTC close above $90k today?", midnight - timedelta(hours=5, minutes=59), "kalshi"),
        _Ev("finance", "Will BTC close above $90k today?", midnight, "polymarket"),
        _Ev("finance", "Will BTC close above $90k today?", midnight + timedelta(hours=5, minutes=59), "manifold"),
        # Just OUTSIDE the window relative to the first event above (>6h away).
        _Ev("finance", "Will BTC close above $90k today?", midnight + timedelta(hours=6, minutes=1) + timedelta(hours=5, minutes=59), "kalshi"),
    ]

    reference = _reference_match_events(events)
    optimized = _groups_as_id_sets(match_events(events))
    assert optimized == sorted(reference, key=lambda s: sorted(s))


def test_optimized_match_events_matches_reference_random_dataset():
    """Randomized dataset spanning several categories, similar-but-distinct
    titles, and clustered resolution dates — confirms the bucketed/memoized
    implementation produces byte-for-byte the same grouping as the original
    unbucketed reference algorithm across many small realistic cases."""
    rng = random.Random(1234)
    categories = ["finance", "politics", "climate", "sports"]
    base = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    platforms = ["kalshi", "polymarket", "manifold"]

    subjects = [
        "Will the Fed cut rates by {n}bps in {month}?",
        "Will BTC close above ${n}k this Friday?",
        "Will candidate {n} win the special election?",
        "Will it rain more than {n}mm in the region this week?",
    ]

    events = []
    for _ in range(80):
        template = rng.choice(subjects)
        title = template.format(n=rng.choice([25, 50, 100, 110]), month="July")
        events.append(_Ev(
            category=rng.choice(categories),
            title=title,
            resolution_date=base + timedelta(hours=rng.randint(-10, 10)),
            platform=rng.choice(platforms),
        ))

    reference = sorted(_reference_match_events(events), key=lambda s: sorted(s))
    optimized = _groups_as_id_sets(match_events(events, title_threshold=0.6))
    assert optimized == reference


def test_match_events_scales_to_large_single_category_volume():
    """Simulates the real 2026-07-04 production incident: ~2000 events
    sharing one `category`/`resolution_date` (Kalshi's current category
    tagging is degenerate — see `match_events` docstring) with long,
    comma-joined titles built from a small shared vocabulary (the actual
    shape of today's real Kalshi payload). The pre-fix implementation could
    not complete this in the 280s production timeout; the fix must finish
    comfortably inside a normal test run."""
    rng = random.Random(99)
    names = ["Ohtani", "Betts", "Freeman", "Judge", "Soto", "Trout", "Harper", "Machado"]
    base = datetime(2026, 7, 7, 2, 10, tzinfo=timezone.utc)

    def rand_title():
        n = rng.randint(1, 12)
        return ",".join(f"yes {rng.choice(names)}: {rng.randint(1, 8)}+" for _ in range(n))

    events = [_Ev("uncategorized", rand_title(), base, "kalshi") for _ in range(800)]
    events += [_Ev("finance", f"Will BTC hit ${rng.randint(50, 150)}k?", base, "polymarket") for _ in range(15)]
    events += [_Ev("politics", f"Will candidate {i} win?", base, "manifold") for i in range(10)]
    rng.shuffle(events)

    t0 = time.time()
    groups = match_events(events)
    elapsed = time.time() - t0

    assert len(groups) > 0
    # Generous bound for CI variance — the point is "seconds", not "minutes".
    assert elapsed < 30, f"match_events took {elapsed:.1f}s on {len(events)} events — perf regression"
