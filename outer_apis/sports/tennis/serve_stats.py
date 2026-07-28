"""
Public call surface for real p_serve-by-rank-bucket calibration — wraps
`outer_apis/scrapers/tennis/match_charting_scraper.py` (real per-match serve
stats, both tours) cross-referenced against `outer_apis/tennis_rankings.py`
(real CURRENT rankings) to produce the calibrated table that
`models/volatility/sports/tennis/tennis_markov.py` needs.

Separation of concerns, same pattern as `outer_apis/tennis_rankings.py`:
`outer_apis/scrapers/tennis/*.py` only does raw HTTP + parsing, this file owns
the calibration logic, fail-closed error handling, and caching.

IMPORTANT limitation, documented honestly (not hidden): the Match
Charting Project's serve stats are historical (matches charted over many
years), but we only have CURRENT rankings (no historical rank-at-match-
time data source is wired). So a player's charted matches are bucketed by
their CURRENT rank, not their rank at the time each historical match was
played. This is an approximation — reasonable for players whose ranking
hasn't moved far, less accurate for players who moved between buckets
over their career (e.g. a now-retired/declined player charted mostly at
their peak). Documented as a TODO for a real historical-rank join if this
ever needs improving.
"""

import difflib
import re
import time
import unicodedata
from typing import Any

from outer_apis.sports.tennis import rankings as tennis_rankings
from database.historical.tennis.writer import save_serve_calibration_snapshot
from outer_apis.scrapers.tennis import match_charting_scraper

_RANKING_LOOKUP_DEPTH = 500  # how deep into the rankings to fetch for the name-match pool.
_FUZZY_MATCH_CUTOFF = 0.85  # stricter than the scrapers' 0.72 — wrong-player matches here silently corrupt calibration stats, not just miss one lookup.


def _norm(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9 ]", " ", ascii_str.lower()).strip()


def _build_name_to_rank(tour: str) -> dict[str, int]:
    """Fetches the top-N rankings ONCE (cached by tennis_rankings' own TTL
    cache) and returns a normalized-name -> rank dict, instead of doing a
    separate HTTP round-trip per player (which would be hundreds of
    requests against the live ATP/WTA sites for a full calibration run)."""
    players = tennis_rankings.get_top_rankings(n=_RANKING_LOOKUP_DEPTH, tour=tour)
    return {_norm(p["nombre"]): p["ranking"] for p in players if p.get("nombre") and p.get("ranking")}


def _lookup_rank(name: str, name_to_rank: dict[str, int]) -> int | None:
    """Fail-closed: no match above `_FUZZY_MATCH_CUTOFF` -> None, never
    guesses a rank for a player outside the fetched top-N pool (e.g.
    retired players, or players who fell out of the rankings since their
    charted matches)."""
    target = _norm(name)
    if target in name_to_rank:
        return name_to_rank[target]
    matches = difflib.get_close_matches(target, list(name_to_rank.keys()), n=1, cutoff=_FUZZY_MATCH_CUTOFF)
    return name_to_rank[matches[0]] if matches else None

# Same buckets as models/volatility/sports/tennis/tennis_markov.py's
# _RANK_BUCKETS — kept as a separate literal here (not imported) so this
# module doesn't depend on the model package; tennis_markov.py is
# responsible for keeping its own bucket edges in sync if it ever changes
# them (documented there too).
_RANK_BUCKETS: list[tuple[int, int]] = [
    (1, 10), (11, 20), (21, 50), (51, 100), (101, 200), (201, 100_000),
]

_MIN_OBS_PER_BUCKET = 5  # fail-closed: a bucket with fewer real match-observations is dropped, not fabricated.

_CACHE_TTL_S = 6 * 3600  # 6h — this is an expensive multi-fetch+cross-reference call, cache aggressively.
_calibration_cache: dict[str, tuple[float, dict]] = {}


def _bucket_for_rank(rank: int) -> tuple[int, int]:
    for lo, hi in _RANK_BUCKETS:
        if lo <= rank <= hi:
            return (lo, hi)
    return _RANK_BUCKETS[-1] if rank > _RANK_BUCKETS[-1][1] else _RANK_BUCKETS[0]


def calibrate_serve_pct_by_bucket(tour: str) -> dict[tuple[int, int], dict[str, Any]]:
    """Real p_serve per rank bucket, calibrated from Match Charting
    Project serve stats bucketed by each player's CURRENT ranking.

    Args:
        tour: "ATP" or "WTA".

    Returns:
        Dict of bucket -> {"p_serve": float, "n_matches": int,
        "n_players": int}. Fail-closed: a bucket with fewer than
        `_MIN_OBS_PER_BUCKET` charted matches is OMITTED from the result
        entirely (never filled with a fabricated/interpolated value) —
        the caller (tennis_markov.py) is responsible for falling back to
        its own static placeholder for any bucket missing here.

        Returns {} entirely if either data source (scraper or rankings)
        fails outright.
    """
    tour = tour.upper()
    if tour not in ("ATP", "WTA"):
        return {}

    cache_key = tour
    cached = _calibration_cache.get(cache_key)
    if cached and (time.monotonic() - cached[0]) < _CACHE_TTL_S:
        return cached[1]

    chart_tour_code = "m" if tour == "ATP" else "w"
    serve_rows = match_charting_scraper.get_serve_basics(chart_tour_code)
    if not serve_rows:
        return {}

    # Aggregate pts/pts_won per player across all their charted matches.
    per_player: dict[str, dict[str, int]] = {}
    for row in serve_rows:
        player = row["player"]
        agg = per_player.setdefault(player, {"pts": 0, "pts_won": 0, "n_matches": 0})
        agg["pts"] += row["pts"]
        agg["pts_won"] += row["pts_won"]
        agg["n_matches"] += 1

    # Look up each player's CURRENT rank (real, live) to assign a bucket.
    # Fetched ONCE for the whole tour (not per player) to avoid hundreds
    # of HTTP round-trips against the live ATP/WTA sites.
    name_to_rank = _build_name_to_rank(tour)
    if not name_to_rank:
        return {}

    # Fail-closed per player: no rank found (retired/not in top-N/name
    # mismatch) -> that player's matches are simply excluded, not
    # guessed into a bucket.
    bucket_sums: dict[tuple[int, int], dict[str, int]] = {
        b: {"pts": 0, "pts_won": 0, "n_matches": 0, "n_players": 0} for b in _RANK_BUCKETS
    }
    for player, agg in per_player.items():
        rank = _lookup_rank(player, name_to_rank)
        if rank is None or rank <= 0:
            continue
        bucket = _bucket_for_rank(rank)
        bucket_sums[bucket]["pts"] += agg["pts"]
        bucket_sums[bucket]["pts_won"] += agg["pts_won"]
        bucket_sums[bucket]["n_matches"] += agg["n_matches"]
        bucket_sums[bucket]["n_players"] += 1

    result: dict[tuple[int, int], dict[str, Any]] = {}
    for bucket, sums in bucket_sums.items():
        if sums["n_matches"] < _MIN_OBS_PER_BUCKET or sums["pts"] <= 0:
            continue  # fail-closed: not enough real data for this bucket.
        result[bucket] = {
            "p_serve": round(sums["pts_won"] / sums["pts"], 6),
            "n_matches": sums["n_matches"],
            "n_players": sums["n_players"],
        }

    _calibration_cache[cache_key] = (time.monotonic(), result)

    # Persiste al historial SOLO en fetch fresco (no en cache hits, ver
    # arriba). Fail-closed internamente, ver database/tennis_historical_writer.py.
    if result:
        save_serve_calibration_snapshot(tour, result)

    return result
