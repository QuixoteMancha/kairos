"""
Public call surface for self-computed NBA team Elo ratings — wraps
`outer_apis/scrapers/basketball/nba_stats_scraper.py` + `database/team_elo_writer.py`.
See `outer_apis/elo_engine.py` for why this is self-computed (no free
pre-computed NBA Elo source exists, unlike tennis/football).
"""

import time
from typing import Any

from outer_apis.scrapers.basketball import nba_stats_scraper
from database.historical.team_sports.writer import compute_and_persist_elo, find_team_rating

SPORT = "basketball"
_CACHE_TTL_S = 3600 * 6
_ratings_cache: dict[str, tuple[float, dict]] = {}


def get_team_ratings(season: str = "2024-25") -> dict[str, float]:
    """Computes (or returns cached) Elo ratings for every NBA team that
    played in `season`, chronologically from real game results.

    Fail-closed: {} if the scraper fails entirely. A single unparseable
    game is skipped (see nba_stats_scraper), never fabricated.
    """
    cache_key = season
    cached = _ratings_cache.get(cache_key)
    if cached and (time.monotonic() - cached[0]) < _CACHE_TTL_S:
        return cached[1]

    try:
        raw_rows = nba_stats_scraper.get_season_games(season=season)
    except Exception:
        raw_rows = []

    games = _pair_games(raw_rows)
    ratings = compute_and_persist_elo(SPORT, games) if games else {}

    _ratings_cache[cache_key] = (time.monotonic(), ratings)
    return ratings


def get_team_rating(team_name: str) -> float | None:
    """Current stored rating for a team, matched by prefix (Kalshi's
    market data gives short names like "Milwaukee" vs the full stored
    "Milwaukee Bucks", see database/team_elo_writer.py::find_team_rating
    docstring) -- fail-closed: None if unknown/ambiguous."""
    return find_team_rating(SPORT, team_name)


def _pair_games(raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """NBA's leaguegamelog gives one row per (game, team) -- pairs rows
    sharing the same game_id into a single {"date", "team_a", "team_b",
    "a_won"} record. Fail-closed: a game_id with != 2 rows (data glitch)
    is skipped."""
    by_game: dict[str, list[dict]] = {}
    for row in raw_rows:
        by_game.setdefault(row["game_id"], []).append(row)

    games: list[dict[str, Any]] = []
    for game_id, rows in by_game.items():
        if len(rows) != 2:
            continue
        a, b = rows
        games.append({
            "date": a["date"],
            "team_a": a["team_name"],
            "team_b": b["team_name"],
            "a_won": a["result"] == "W",
            "source": "nba_stats",
        })
    return games
