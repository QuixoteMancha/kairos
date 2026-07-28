"""
Public call surface for self-computed MLS team Elo ratings — wraps
`outer_apis/scrapers/football/mls_scraper.py` (ESPN's public scoreboard API) +
`outer_apis/scrapers/football/mls_teams.py` (name translation) +
`database/historical/team_sports/writer.py`.

WHY A SEPARATE MODULE FROM football_ratings.py: ClubElo (which
`outer_apis/football_ratings.py` wraps) has ZERO MLS coverage -- confirmed
2026-07-21, it's exclusively European clubs. MLS needs its own real
data source (ESPN, self-computed via `outer_apis/elo_engine.py`, same pattern
as NBA/MLB/NHL -- see that module's docstring for why those 3 sports are
self-computed). Both this module and football_ratings.py write into the
SAME `TeamRating` table with `sport="football"` (MLS and European club
names never collide) but this module tags its rows with
`source="mls_elo"` (vs ClubElo's `source="kairos_elo_engine"`... actually
football_ratings.py doesn't self-compute, see below) so the two datasets
stay auditable/distinguishable per row.

`outer_apis/kalshi_football_markets.py` tries `football_ratings.get_team_rating`
first (ClubElo, real European coverage) and falls back to
`mls_ratings.get_team_rating` (this module) only if that returns None --
in practice that's every MLS team today.
"""

from outer_apis.scrapers.football import mls_scraper
from outer_apis.scrapers.football.mls_teams import canonical_name
from database.historical.team_sports.writer import compute_and_persist_elo, get_current_ratings, find_rating_by_prefix

SPORT = "football"  # SAME bucket as ClubElo -- see module docstring, no name collisions
SOURCE = "mls_elo"
_DB_FRESHNESS_HOURS = 6.0


def get_team_ratings(days_back: int = 365) -> dict[str, float]:
    """Computes (or reads fresh-enough from DB) Elo ratings from real
    completed MLS games over the last `days_back` days (default ~1
    season+, MLS plays Feb-Nov). Fail-closed: {} if the scraper fails
    entirely; a game whose team name isn't in the static ESPN->canonical
    mapping is skipped (never guesses a team identity, e.g. a future
    expansion/rebrand not yet added to mls_teams.py)."""
    cached = get_current_ratings(SPORT, source=SOURCE, max_age_hours=_DB_FRESHNESS_HOURS)
    if cached is not None:
        return cached

    try:
        raw_games = mls_scraper.get_completed_games(days_back=days_back)
    except Exception:
        raw_games = []

    games = []
    for g in raw_games:
        home_name = canonical_name(g["home_team"])
        away_name = canonical_name(g["away_team"])
        if not home_name or not away_name:
            continue
        if g["home_score"] == g["away_score"]:
            continue  # draws are common in soccer; elo_engine has no-draw update_ratings, skip ties
        games.append({
            "date": g["date"],
            "team_a": home_name,
            "team_b": away_name,
            "a_won": g["home_score"] > g["away_score"],
            "source": SOURCE,
        })
    return compute_and_persist_elo(SPORT, games, rating_source=SOURCE) if games else {}


def get_team_rating(team_name: str) -> float | None:
    """Prefix-matched against the SAME `sport="football"` bucket ClubElo
    uses (see module docstring). Routes through `get_team_ratings()`
    (2026-07-28 fix, see find_rating_by_prefix docstring): MLS happened
    to stay fresh anyway via `workers/mls_worker.py`'s 6h refresh, but
    this lookup itself never triggered a refresh -- fixed for defense in
    depth in case that worker's cadence ever drifts or stops. Fail-closed:
    None if unknown/ambiguous."""
    return find_rating_by_prefix(get_team_ratings(), team_name)
