"""
Public call surface for self-computed MLB team Elo ratings — wraps
`outer_apis/scrapers/baseball/mlb_stats_scraper.py` + `database/team_elo_writer.py`.
See `outer_apis/elo_engine.py` for why this is self-computed.
"""

from datetime import date, timedelta

from outer_apis.scrapers.baseball import mlb_stats_scraper
from database.historical.team_sports.writer import compute_and_persist_elo, get_current_ratings, find_rating_by_prefix

SPORT = "baseball"
SOURCE = "kairos_elo_engine"
_DB_FRESHNESS_HOURS = 6.0


def get_team_ratings(days_back: int = 45) -> dict[str, float]:
    """Computes (or reads fresh-enough from DB) Elo ratings from the last
    `days_back` days of completed MLB games. Fail-closed: {} if the
    scraper fails."""
    cached = get_current_ratings(SPORT, source=SOURCE, max_age_hours=_DB_FRESHNESS_HOURS)
    if cached is not None:
        return cached

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
    return compute_and_persist_elo(SPORT, games) if games else {}


def get_team_rating(team_name: str) -> float | None:
    """Prefix-matched against `get_team_ratings()` (2026-07-28 fix, see
    find_rating_by_prefix docstring: routes through the same DB-freshness
    + refresh path as the plural function, instead of a stale read-only
    DB query -- real bug found live, this lookup ran on every scan via
    the market builders but never refreshed ratings). Fail-closed: None
    if unknown/ambiguous."""
    return find_rating_by_prefix(get_team_ratings(), team_name)
