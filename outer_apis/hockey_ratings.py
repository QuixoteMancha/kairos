"""
Public call surface for self-computed NHL team Elo ratings — wraps
`outer_apis/scrapers/hockey/nhl_stats_scraper.py` + `database/team_elo_writer.py`.
See `outer_apis/elo_engine.py` for why this is self-computed.
"""

import time

from outer_apis.scrapers.hockey import nhl_stats_scraper
from database.historical.team_sports.writer import compute_and_persist_elo, find_team_rating

SPORT = "hockey"
_CACHE_TTL_S = 3600 * 6
_ratings_cache: dict[str, tuple[float, dict]] = {}


def get_team_ratings(weeks_back: int = 10) -> dict[str, float]:
    """Computes (or returns cached) Elo ratings from the last `weeks_back`
    weeks of completed NHL games (bounded, see nhl_stats_scraper's
    docstring re: no single season-wide endpoint). Fail-closed: {} if
    the scraper fails."""
    cache_key = str(weeks_back)
    cached = _ratings_cache.get(cache_key)
    if cached and (time.monotonic() - cached[0]) < _CACHE_TTL_S:
        return cached[1]

    try:
        raw_games = nhl_stats_scraper.get_recent_games(weeks=weeks_back)
    except Exception:
        raw_games = []

    games = [
        {
            "date": g["date"],
            "team_a": g["home_team"],
            "team_b": g["away_team"],
            "a_won": g["home_won"],
            "source": "nhl_stats",
        }
        for g in raw_games
    ]
    ratings = compute_and_persist_elo(SPORT, games) if games else {}

    _ratings_cache[cache_key] = (time.monotonic(), ratings)
    return ratings


def get_team_rating(team_name: str) -> float | None:
    """Prefix-matched, see basketball_ratings.py::get_team_rating and
    database/team_elo_writer.py::find_team_rating docstrings. Fail-closed:
    None if unknown/ambiguous."""
    return find_team_rating(SPORT, team_name)
