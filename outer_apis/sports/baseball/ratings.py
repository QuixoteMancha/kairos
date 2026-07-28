"""
Public call surface for self-computed MLB team Elo ratings — wraps
`outer_apis/scrapers/baseball/mlb_stats_scraper.py` + `database/team_elo_writer.py`.
See `outer_apis/elo_engine.py` for why this is self-computed.
"""

import time
from datetime import date, timedelta

from outer_apis.scrapers.baseball import mlb_stats_scraper
from database.historical.team_sports.writer import compute_and_persist_elo, find_team_rating

SPORT = "baseball"
_CACHE_TTL_S = 3600 * 6
_ratings_cache: dict[str, tuple[float, dict]] = {}


def get_team_ratings(days_back: int = 45) -> dict[str, float]:
    """Computes (or returns cached) Elo ratings from the last `days_back`
    days of completed MLB games. Fail-closed: {} if the scraper fails."""
    cache_key = str(days_back)
    cached = _ratings_cache.get(cache_key)
    if cached and (time.monotonic() - cached[0]) < _CACHE_TTL_S:
        return cached[1]

    end = date.today()
    start = end - timedelta(days=days_back)
    try:
        raw_games = mlb_stats_scraper.get_games_in_range(start.isoformat(), end.isoformat())
    except Exception:
        raw_games = []

    games = [
        {
            "date": g["date"],
            "team_a": g["home_team"],
            "team_b": g["away_team"],
            "a_won": g["home_won"],
            "source": "mlb_stats",
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
