"""
Public call surface for self-computed NFL team Elo ratings — wraps
`outer_apis/scrapers/american_football/nflverse_scraper.py` +
`database/team_elo_writer.py`. Sport string "american_football" matches
the convention already used by the rescued nflverse-sourced TeamRating
rows from the old system (real, fresh data found already in the volume,
see kairos-team-sports-tables-and-research-2026-07-21 memory).

NAMING FIX (2026-07-21, found while wiring real Kalshi NFL markets):
nflverse's `games.csv` (and the old rescued rows) identify teams by CODE
("SEA", "DEN"), but Kalshi's market data gives full city names
("Seattle", "Dallas") -- unlike MLB/NBA/NHL (where our stored full names
and Kalshi's short names are prefix-compatible, handled generically by
`database/team_elo_writer.py::find_team_rating`), there's no prefix
relationship between a code and a city name at all. Games are translated
to full names (via `outer_apis/scrapers/american_football/nfl_teams.py`'s
static mapping) BEFORE being fed to the Elo engine, so TeamRating stores
full names consistently with the other 3 team sports, and the generic
prefix matcher works here too instead of needing a 4th different lookup
strategy.
"""

import time

from outer_apis.scrapers.american_football import nflverse_scraper
from outer_apis.scrapers.american_football.nfl_teams import code_to_name
from database.historical.team_sports.writer import compute_and_persist_elo, find_team_rating

SPORT = "american_football"
_CACHE_TTL_S = 3600 * 6
_ratings_cache: dict[str, tuple[float, dict]] = {}


def get_team_ratings(seasons: tuple[str, ...] = ("2024", "2025")) -> dict[str, float]:
    """Computes (or returns cached) Elo ratings from real regular-season
    NFL games for the given seasons. Fail-closed: {} if the scraper
    fails entirely; a game whose team code isn't recognized is skipped
    (never guesses a team identity)."""
    cache_key = ",".join(seasons)
    cached = _ratings_cache.get(cache_key)
    if cached and (time.monotonic() - cached[0]) < _CACHE_TTL_S:
        return cached[1]

    try:
        raw_games = nflverse_scraper.get_games(seasons=seasons)
    except Exception:
        raw_games = []

    games = []
    for g in raw_games:
        home_name = code_to_name(g["home_team"])
        away_name = code_to_name(g["away_team"])
        if not home_name or not away_name:
            continue
        games.append({
            "date": g["date"],
            "team_a": home_name,
            "team_b": away_name,
            "a_won": g["home_won"],
            "source": "nflverse",
        })
    ratings = compute_and_persist_elo(SPORT, games) if games else {}

    _ratings_cache[cache_key] = (time.monotonic(), ratings)
    return ratings


def get_team_rating(team_name: str) -> float | None:
    """Prefix-matched, see database/team_elo_writer.py::find_team_rating
    docstring. Fail-closed: None if unknown/ambiguous."""
    return find_team_rating(SPORT, team_name)
