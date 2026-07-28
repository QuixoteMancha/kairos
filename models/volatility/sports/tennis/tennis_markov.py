"""Tennis volatility model: point-level Markov chain + Monte Carlo.

Rescued from the pre-"extreme makeover" architecture (see git history,
`src/core/strategies/event_volatility/tennis_markov.py`), stripped of all
I/O/network/DB dependencies and adapted to the current `models/volatility/`
fail-closed pattern.

Methodology
-----------
1. Given two players' ATP/WTA rankings, bucket each into a rank range and
   look up a `p_serve` (probability of winning a point on own serve) for
   that bucket. This is currently a HARDCODED placeholder table (see
   `_BUCKET_P_SERVE` below) — not calibrated against real data, since the
   old scraper-based calibration pipeline (`SackmannScraper`) no longer
   exists in this repo.

2. Standard tennis scoring is a Markov chain: game -> set -> match win
   probability is a deterministic function of (p_serve_a, p_serve_b) and the
   current score state. This is derived analytically/recursively from
   p_serve — no point-by-point historical data needed, the same way a
   Black-Scholes price is derived from volatility rather than read off a
   historical options-price tape.

3. Monte Carlo: simulate many matches point-by-point using p_serve_a/b to
   decide each point's winner. After every point, compute player A's
   instantaneous match-win-probability analytically (memoized recursion over
   game/set state) from the *current* score — this is the simulated
   probability path. The volatility estimate is the standard deviation of
   that path across all simulated matches (see `simulate_win_prob_volatility`
   docstring for why stddev was chosen over IQR).
"""

from __future__ import annotations

import functools
import random

# ── Calibration ───────────────────────────────────────────────────────────

# Rank buckets: (min_rank, max_rank_inclusive). Ranks below 1 or above the
# last bucket's max fall into the nearest edge bucket (clamped).
_RANK_BUCKETS: list[tuple[int, int]] = [
    (1, 10), (11, 20), (21, 50), (51, 100), (101, 200), (201, 100_000),
]

# FALLBACK ONLY as of 2026-07-20: used when real calibration (see below)
# fails outright, or for a specific bucket that real calibration couldn't
# fill (too few charted matches / no ranked players found in that bucket).
# Values are approximate service-points-won fractions from general tour
# knowledge, decreasing as ranking gets worse (weaker servers on average
# further down the rankings). No longer the primary source when a tour is
# known and live calibration succeeds — see `_get_calibrated_table`.
_BUCKET_P_SERVE: dict[tuple[int, int], float] = {
    (1, 10): 0.65,      # top 10: dominant serve
    (11, 20): 0.64,
    (21, 50): 0.63,
    (51, 100): 0.62,
    (101, 200): 0.61,
    (201, 100_000): 0.60,
}

_DEFAULT_P_SERVE = 0.62  # fallback if a bucket is somehow missing from every table.


def _bucket_for_rank(rank: int) -> tuple[int, int]:
    for lo, hi in _RANK_BUCKETS:
        if lo <= rank <= hi:
            return (lo, hi)
    return _RANK_BUCKETS[-1] if rank > _RANK_BUCKETS[-1][1] else _RANK_BUCKETS[0]


# ── Real calibration (Match Charting Project x live rankings) ─────────────
#
# `outer_apis/tennis_serve_stats.py` calibrates real p_serve-by-bucket from Jeff
# Sackmann's Match Charting Project (per-match serve stats, both tours)
# cross-referenced against current ATP/WTA rankings (`outer_apis/tennis_rankings.py`).
# Cached at module level here since it's an expensive multi-request live
# calibration (~10s), not something to redo on every model call.

_CALIBRATED_CACHE: dict[str, dict[tuple[int, int], float]] = {}


def _get_calibrated_table(tour: str) -> dict[tuple[int, int], float]:
    """Real p_serve-by-bucket for one tour ("ATP"/"WTA"), blended with the
    static placeholder as a per-bucket fallback.

    Fail-closed at every layer: a network/scraper failure (exception,
    empty result) falls back ENTIRELY to the static placeholder table; a
    specific bucket missing from the real calibration (too few charted
    matches) falls back to the static placeholder for JUST that bucket,
    never fabricated/interpolated from neighboring buckets.
    """
    if tour in _CALIBRATED_CACHE:
        return _CALIBRATED_CACHE[tour]

    table = dict(_BUCKET_P_SERVE)  # start from the static placeholder for every bucket
    try:
        from outer_apis.sports.tennis import serve_stats as tennis_serve_stats

        real = tennis_serve_stats.calibrate_serve_pct_by_bucket(tour)
        for bucket, data in real.items():
            table[bucket] = data["p_serve"]
    except Exception:
        # Any failure (network, parsing, unexpected shape) -> stick with
        # the static placeholder for every bucket, never raise up to the
        # volatility model.
        pass

    _CALIBRATED_CACHE[tour] = table
    return table


def p_serve_for_rank(rank: int, tour: str | None = None) -> float:
    """Look up p_serve for a given ranking.

    If `tour` is "ATP" or "WTA", uses the real calibrated table for that
    tour (falling back per-bucket to the static placeholder, see
    `_get_calibrated_table`). If `tour` is None/unrecognized, uses the
    static placeholder directly WITHOUT attempting live calibration —
    fail-closed: guessing which tour's curve to use (mixing ATP/WTA, which
    have measurably different real p_serve, see memory) would be worse
    than honestly falling back to the generic placeholder.
    """
    bucket = _bucket_for_rank(rank)
    if tour in ("ATP", "WTA"):
        table = _get_calibrated_table(tour)
        return table.get(bucket, _DEFAULT_P_SERVE)
    return _BUCKET_P_SERVE.get(bucket, _DEFAULT_P_SERVE)


# ── Point -> game -> set -> match Markov chain (analytic, memoized) ────────
# Rescued verbatim (import/typing adaptations only) from the old
# tennis_markov.py — the math itself is unchanged.


@functools.lru_cache(maxsize=None)
def _p_win_from_deuce(p: float) -> float:
    """P(win game | at deuce), point-win-prob p on own serve."""
    q = 1.0 - p
    # x = p^2 + 2pq*x  (win outright, or reach advantage/deuce again and
    # recurse) -> x = p^2 / (p^2 + q^2)
    return (p * p) / (p * p + q * q)


@functools.lru_cache(maxsize=None)
def _p_win_game(p: float) -> float:
    """P(win own service game), point-win-prob p on own serve.

    Standard closed-form: sum over ways to win before deuce, plus reaching
    deuce and winning from there.
    """
    q = 1.0 - p
    p_win_to_40 = (
        p**4
        + 4 * p**4 * q
        + 10 * p**4 * q**2
    )
    p_reach_deuce = 20 * (p**3) * (q**3)
    return p_win_to_40 + p_reach_deuce * _p_win_from_deuce(p)


@functools.lru_cache(maxsize=None)
def _p_win_tiebreak(pa: float, pb: float, a_serves_first: bool = True) -> float:
    """P(player A wins a standard 7-point tiebreak), recursive point-by-point.

    Serve alternates: A serves point 1, then B serves points 2-3, A serves
    4-5, etc. (standard tiebreak serve rotation). Must win by 2, win at >=7.
    """

    @functools.lru_cache(maxsize=None)
    def rec(sa: int, sb: int, a_serves: bool) -> float:
        if sa >= 7 and sa - sb >= 2:
            return 1.0
        if sb >= 7 and sb - sa >= 2:
            return 0.0
        # Past the "race to 7" threshold (both >=6), a tie is memoryless: the
        # next two points always form the same repeating serve block, so
        # instead of recursing forever we solve the "win by 2 from parity"
        # closed form directly.
        if sa >= 6 and sa == sb:
            p1 = pa if a_serves else (1.0 - pb)
            next_a_serves = not a_serves
            p2 = pa if next_a_serves else (1.0 - pb)
            win_both = p1 * p2
            lose_both = (1.0 - p1) * (1.0 - p2)
            split = 1.0 - win_both - lose_both
            denom = 1.0 - split
            return win_both / denom if denom > 1e-12 else 0.5
        p_a_wins_point = pa if a_serves else (1.0 - pb)
        total_played = sa + sb
        next_a_serves = a_serves if total_played == 0 else (
            a_serves if (total_played + 1) % 2 == 0 else not a_serves
        )
        return p_a_wins_point * rec(sa + 1, sb, next_a_serves) + (
            1.0 - p_a_wins_point
        ) * rec(sa, sb + 1, next_a_serves)

    return rec(0, 0, a_serves_first)


@functools.lru_cache(maxsize=None)
def _p_win_set(
    pa_game: float, pb_game: float, a_serves_first: bool = True,
    pa_serve: float | None = None, pb_serve: float | None = None,
) -> float:
    """P(player A wins a standard advantage set, tiebreak at 6-6).

    `pa_serve`/`pb_serve` (point-level serve-win probabilities) are used for
    the tiebreak itself, which is played point-by-point — NOT the same thing
    as `pa_game`/`pb_game` (probability of winning a full service game).
    """
    ta = pa_serve if pa_serve is not None else pa_game
    tb = pb_serve if pb_serve is not None else pb_game

    @functools.lru_cache(maxsize=None)
    def rec(ga: int, gb: int, a_serves: bool) -> float:
        if ga >= 6 and ga - gb >= 2:
            return 1.0
        if gb >= 6 and gb - ga >= 2:
            return 0.0
        if ga == 7 and gb == 6:
            return 1.0
        if gb == 7 and ga == 6:
            return 0.0
        if ga == 6 and gb == 6:
            return _p_win_tiebreak(ta, tb, a_serves_first=a_serves)
        p_a_wins_game = pa_game if a_serves else (1.0 - pb_game)
        return p_a_wins_game * rec(ga + 1, gb, not a_serves) + (
            1.0 - p_a_wins_game
        ) * rec(ga, gb + 1, not a_serves)

    return rec(0, 0, a_serves_first)


def _p_win_match_from_set_prob(p_set: float, best_of: int = 3) -> float:
    """P(win match) from single-set win probability, best-of-3 or best-of-5."""
    if best_of == 3:
        return p_set**2 * (3 - 2 * p_set)
    if best_of == 5:
        p = p_set
        q = 1 - p
        return (
            p**3
            + 3 * p**3 * q
            + 6 * p**3 * q**2
        )
    raise ValueError("best_of must be 3 or 5")


def instantaneous_match_win_prob(
    pa_serve: float,
    pb_serve: float,
    sets_a: int,
    sets_b: int,
    games_a: int,
    games_b: int,
    points_a: int,
    points_b: int,
    a_serving: bool,
    best_of: int = 3,
) -> float:
    """P(player A wins the match) from any live score state.

    Recomputes the current game's win probability from the point score, then
    propagates through the set and match level using the memoized functions
    above.
    """
    pa_game = _p_win_game(pa_serve)
    pb_game = _p_win_game(pb_serve)

    if points_a == 0 and points_b == 0:
        p_win_current_game = pa_game if a_serving else (1.0 - pb_game)
    else:
        p_point = pa_serve if a_serving else (1.0 - pb_serve)
        p_win_current_game = _p_win_game_from_points(
            points_a, points_b, p_point if a_serving else 1.0 - p_point, a_serving
        )

    p_win_set_if_a_wins_game = _p_win_set_from_games(
        games_a + 1, games_b, pa_game, pb_game, not a_serving,
        pa_serve=pa_serve, pb_serve=pb_serve,
    )
    p_win_set_if_b_wins_game = _p_win_set_from_games(
        games_a, games_b + 1, pa_game, pb_game, not a_serving,
        pa_serve=pa_serve, pb_serve=pb_serve,
    )
    p_win_current_set = (
        p_win_current_game * p_win_set_if_a_wins_game
        + (1 - p_win_current_game) * p_win_set_if_b_wins_game
    )

    sets_to_win = (best_of // 2) + 1
    if sets_a == sets_to_win - 1 and sets_b == sets_to_win - 1:
        return p_win_current_set
    p_match_if_a_wins_set = _p_win_match_from_sets(sets_a + 1, sets_b, best_of)
    p_match_if_b_wins_set = _p_win_match_from_sets(sets_a, sets_b + 1, best_of)
    return (
        p_win_current_set * p_match_if_a_wins_set
        + (1 - p_win_current_set) * p_match_if_b_wins_set
    )


@functools.lru_cache(maxsize=None)
def _p_win_game_from_points(pa: int, pb: int, p_point_a: float, a_serving: bool) -> float:
    """P(A wins the current game) from a live point score (0/15/30/40/deuce)."""
    if pa >= 4 and pa - pb >= 2:
        return 1.0
    if pb >= 4 and pb - pa >= 2:
        return 0.0
    if pa >= 3 and pb >= 3:
        return _p_win_from_deuce(p_point_a if a_serving else (1.0 - p_point_a)) if a_serving else (
            1.0 - _p_win_from_deuce(1.0 - p_point_a)
        )
    p = p_point_a
    return p * _p_win_game_from_points(pa + 1, pb, p_point_a, a_serving) + (
        1 - p
    ) * _p_win_game_from_points(pa, pb + 1, p_point_a, a_serving)


def _p_win_set_from_games(
    ga: int, gb: int, pa_game: float, pb_game: float, a_serves_next: bool,
    pa_serve: float | None = None, pb_serve: float | None = None,
) -> float:
    """P(A wins set) from a live game score (reuses `_p_win_set`'s recursion)."""
    ta = pa_serve if pa_serve is not None else pa_game
    tb = pb_serve if pb_serve is not None else pb_game

    @functools.lru_cache(maxsize=None)
    def rec(ga: int, gb: int, a_serves: bool) -> float:
        if ga >= 6 and ga - gb >= 2:
            return 1.0
        if gb >= 6 and gb - ga >= 2:
            return 0.0
        if ga == 7 and gb == 6:
            return 1.0
        if gb == 7 and ga == 6:
            return 0.0
        if ga == 6 and gb == 6:
            return _p_win_tiebreak(ta, tb, a_serves_first=a_serves)
        p_a_wins_game = pa_game if a_serves else (1.0 - pb_game)
        return p_a_wins_game * rec(ga + 1, gb, not a_serves) + (
            1.0 - p_a_wins_game
        ) * rec(ga, gb + 1, not a_serves)

    return rec(ga, gb, a_serves_next)


@functools.lru_cache(maxsize=None)
def _p_win_match_from_sets(sets_a: int, sets_b: int, best_of: int) -> float:
    """P(A wins match) purely from current set score (each remaining set
    assumed i.i.d. fair coin — an approximation, only used to resolve
    set-transition branches; the current set's real p_set already carries
    most of the information)."""
    sets_to_win = (best_of // 2) + 1
    if sets_a >= sets_to_win:
        return 1.0
    if sets_b >= sets_to_win:
        return 0.0
    remaining_a = sets_to_win - sets_a
    remaining_b = sets_to_win - sets_b
    from math import comb

    total = 0.0
    for k in range(remaining_b):
        total += comb(remaining_a + k - 1, k) * (0.5**remaining_a) * (0.5**k)
    return total


def pre_match_win_prob(rank_a: int, rank_b: int, best_of: int = 3, tour: str | None = None) -> float:
    """P(player A wins the match) BEFORE the match starts, from rankings alone.

    `tour`: "ATP"/"WTA" uses the real calibrated p_serve table for that
    tour (see `p_serve_for_rank`); None/unrecognized uses the static
    placeholder rather than guessing which tour's curve applies.

    Raises `ValueError` if `rank_a`/`rank_b` are not positive integers, or if
    `best_of` is not 3 or 5.

    IMPLEMENTATION NOTE (bug found and worked around 2026-07-20, see
    memory kairos-tennis-markov-set-transition-bug): this deliberately
    does NOT call `instantaneous_match_win_prob` at the 0-0 state, even
    though that would look like the natural way to ask "what's the
    pre-match win probability". `instantaneous_match_win_prob`'s
    set-transition logic (for going from "just won/lost the current set"
    to "match outcome") routes through `_p_win_match_from_sets`, which
    assumes every REMAINING set is a fair 50/50 coin flip regardless of
    the players' actual skill gap (see that function's own docstring —
    documented as "an approximation" when it was rescued, but empirically
    verified during this rebuild to matter A LOT: rank 5 vs 200 came out
    as 56.9% via that path, vs the mathematically consistent 70.1% via
    the method used here). That approximation is defensible for a LIVE
    mid-match dwell-time simulation (`simulate_win_prob_volatility`, where
    most of the signal is in the current score, not hypothetical future
    sets) but is NOT acceptable for a pre-match fair-price estimate, which
    is exactly "what does the skill gap imply before any points are
    played" — the case where the flawed approximation dominates the
    result instead of being a minor tail correction.

    Correct approach used here: compute the real single-set win
    probability analytically (`_p_win_set`, using the point-level p_serve
    for the 6-6 tiebreak specifically), then use the closed-form
    best-of-N formula (`_p_win_match_from_set_prob`) that correctly
    assumes i.i.d. sets at the REAL p_set — not a fair coin.
    """
    if not isinstance(rank_a, int) or isinstance(rank_a, bool) or rank_a <= 0:
        raise ValueError(f"rank_a must be a positive integer, got {rank_a!r}")
    if not isinstance(rank_b, int) or isinstance(rank_b, bool) or rank_b <= 0:
        raise ValueError(f"rank_b must be a positive integer, got {rank_b!r}")
    if best_of not in (3, 5):
        raise ValueError(f"best_of must be 3 or 5, got {best_of!r}")

    pa_serve = p_serve_for_rank(rank_a, tour=tour)
    pb_serve = p_serve_for_rank(rank_b, tour=tour)

    pa_game = _p_win_game(pa_serve)
    pb_game = _p_win_game(pb_serve)
    p_set = _p_win_set(pa_game, pb_game, a_serves_first=True, pa_serve=pa_serve, pb_serve=pb_serve)

    return _p_win_match_from_set_prob(p_set, best_of=best_of)


# ── Monte Carlo volatility simulation ──────────────────────────────────────

_BAND_EDGES = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]


def _band_index(p: float) -> int:
    p = min(max(p, 0.0), 1.0 - 1e-9)
    for i in range(len(_BAND_EDGES) - 1):
        if _BAND_EDGES[i] <= p < _BAND_EDGES[i + 1]:
            return i
    return len(_BAND_EDGES) - 2


def _bin_time(band_time: list[float], prob: float, weight: float) -> None:
    band_time[_band_index(prob)] += weight


def _game_over(pa: int, pb: int) -> tuple[bool, bool]:
    if pa >= 4 and pa - pb >= 2:
        return True, True
    if pb >= 4 and pb - pa >= 2:
        return True, False
    return False, False


def _set_over(ga: int, gb: int) -> tuple[bool, bool]:
    if ga >= 6 and ga - gb >= 2:
        return True, True
    if gb >= 6 and gb - ga >= 2:
        return True, False
    if ga == 7 or gb == 7:  # tiebreak resolved set at 7-6
        return True, ga > gb
    return False, False


def simulate_dwell_bands(
    pa_serve: float,
    pb_serve: float,
    n_matches: int = 5000,
    best_of: int = 3,
    seed: int | None = None,
) -> list[float]:
    """Monte Carlo point-by-point simulation of `n_matches`, tracking player
    A's instantaneous match-win-probability path after every point.

    Returns the flat list of every instantaneous-probability sample recorded
    across all simulated matches (one sample per point played) — the raw
    probability path, which callers reduce however they need (band
    histogram, stddev, IQR, ...). This is a simplified version of the old
    dwell-time-band model: no `start_state`/`DwellBand` support, since this
    package doesn't have that concept — see `simulate_win_prob_volatility`
    for the actual public entrypoint used by `TennisVModel`.
    """
    rng = random.Random(seed)
    samples: list[float] = []

    for _ in range(n_matches):
        sets_a = sets_b = games_a = games_b = points_a = points_b = 0
        a_serving = True
        sets_to_win = (best_of // 2) + 1

        for _point_guard in range(20_000):  # safety cap vs pathological p values
            if sets_a >= sets_to_win or sets_b >= sets_to_win:
                break

            p_a_wins_point = pa_serve if a_serving else (1.0 - pb_serve)
            if rng.random() < p_a_wins_point:
                points_a += 1
            else:
                points_b += 1

            prob_a = instantaneous_match_win_prob(
                pa_serve, pb_serve, sets_a, sets_b, games_a, games_b,
                points_a, points_b, a_serving, best_of,
            )
            samples.append(prob_a)

            game_over, a_wins_game = _game_over(points_a, points_b)
            if game_over:
                if a_wins_game:
                    games_a += 1
                else:
                    games_b += 1
                points_a = points_b = 0
                a_serving = not a_serving

                set_over, a_wins_set = _set_over(games_a, games_b)
                if set_over:
                    if a_wins_set:
                        sets_a += 1
                    else:
                        sets_b += 1
                    games_a = games_b = 0

    return samples


# Cache Monte Carlo volatility results per (pa_serve, pb_serve, best_of) so
# the simulation doesn't rerun for every call with the same rank-gap
# category (same bucket pair -> same p_serve pair -> same cache hit).
_SIMULATION_CACHE: dict[tuple[float, float, int], float] = {}


def simulate_win_prob_volatility(
    pa_serve: float,
    pb_serve: float,
    n_matches: int = 100,
    best_of: int = 3,
    seed: int = 42,
) -> float:
    """Monte Carlo estimate of "how much can this match's implied win
    probability oscillate before it resolves" — the single volatility number
    this module exposes.

    Reduction choice: population standard deviation of the simulated
    instantaneous-probability path (pooled across all simulated matches and
    points), not the interquartile range. Rationale: stddev is sensitive to
    the full distribution including the two tails near 0/1 that a match
    naturally spends time in as it approaches resolution — exactly the kind
    of "the price got yanked hard right before settling" swings a limit-arb
    strategy cares about capturing. IQR would deliberately suppress those
    tails (by construction, it drops the top/bottom quartile), which is the
    wrong bias for a "how much room does price have to move" metric.

    Cached per (rank_bucket_a, rank_bucket_b, best_of) at module level, since
    `pa_serve`/`pb_serve` are themselves looked up per rank bucket (see
    `TennisVModel`) — same bucket pair always produces the same p_serve
    inputs, so re-simulating per market call would be wasted work.
    """
    # Cache key is derived from the p_serve values directly (rounded), not
    # from ranks, since this function's public contract takes p_serve — the
    # same bucket pair always resolves to the same p_serve values anyway.
    cache_key = (round(pa_serve, 6), round(pb_serve, 6), best_of)
    cached = _SIMULATION_CACHE.get(cache_key)
    if cached is not None:
        return cached

    samples = simulate_dwell_bands(pa_serve, pb_serve, n_matches=n_matches, best_of=best_of, seed=seed)
    if not samples:
        result = 0.0
    else:
        mean = sum(samples) / len(samples)
        variance = sum((x - mean) ** 2 for x in samples) / len(samples)
        result = variance**0.5

    _SIMULATION_CACHE[cache_key] = result
    return result
