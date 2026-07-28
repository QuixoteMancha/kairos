"""
Public call surface for self-computed NHL team Elo ratings — wraps
`outer_apis/scrapers/hockey/nhl_stats_scraper.py` + `database/team_elo_writer.py`.
See `outer_apis/elo_engine.py` for why this is self-computed.
"""

from outer_apis.scrapers.hockey import nhl_stats_scraper
from database.historical.team_sports.writer import compute_and_persist_elo, get_current_ratings, find_rating_by_prefix

SPORT = "hockey"
SOURCE = "kairos_elo_engine"
_DB_FRESHNESS_HOURS = 6.0


def get_team_ratings(weeks_back: int = 10) -> dict[str, float]:
    """Computes (or reads fresh-enough from DB) Elo ratings from the last
    `weeks_back` weeks of completed NHL games (bounded, see
    nhl_stats_scraper's docstring re: no single season-wide endpoint).
    Fail-closed: {} if the scraper fails."""
    cached = get_current_ratings(SPORT, source=SOURCE, max_age_hours=_DB_FRESHNESS_HOURS)
    if cached is not None:
        return cached

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
    return compute_and_persist_elo(SPORT, games) if games else {}


def get_team_rating(team_name: str) -> float | None:
    """Prefix-matched, routes through `get_team_ratings()` (2026-07-28
    fix, see find_rating_by_prefix docstring for why -- this lookup runs
    on every scan but used to never trigger a refresh). Fail-closed: None
    if unknown/ambiguous."""
    return find_rating_by_prefix(get_team_ratings(), team_name)
