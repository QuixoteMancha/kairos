"""
Shared Elo-computation + persistence logic for basketball/baseball/hockey
(the 3 sports with NO pre-computed rating source, unlike tennis/football
— see APIs/elo_engine.py docstring). Each sport's facade
(APIs/basketball_ratings.py, APIs/baseball_ratings.py,
APIs/hockey_ratings.py) converts its own scraper's raw game shape into a
common `{"date", "team_a", "team_b", "a_won"}` list and calls
`compute_and_persist_elo` here, instead of tripling this logic 3x.
"""

from datetime import datetime, timezone
from typing import Any

from peewee import EXCLUDED

from APIs import elo_engine
from database.db import init_db_historical
from database.historical.team_sports.teamrating import TeamRating
from database.historical.team_sports.teammatchhistory import TeamMatchHistory


def find_team_rating(sport: str, short_name: str) -> float | None:
    """Looks up a team's Elo rating by a possibly-SHORT/partial name (e.g.
    Kalshi's `yes_sub_title` gives "Milwaukee", "Kansas City", "Los
    Angeles R" -- not the full stored names like "Milwaukee Brewers",
    "Kansas City Royals"/"Kansas City Chiefs", "Los Angeles Rams", found
    live 2026-07-21 verifying MLB/NFL market pairing).

    Matches by PREFIX (`name.startswith(short_name)`) within the given
    sport, case-insensitive. Fail-closed: requires EXACTLY ONE match --
    zero matches (unknown team) or more than one (genuinely ambiguous
    short name) both return None rather than guessing. This is safe
    because Kalshi's short names are consistently disambiguating prefixes
    in practice (e.g. "Los Angeles R" only prefix-matches "Los Angeles
    Rams", not "Los Angeles Chargers") -- if that assumption ever breaks
    for a specific league, this fails closed instead of picking wrong.
    """
    try:
        init_db_historical()
        target = short_name.strip().lower()
        candidates = [
            r for r in TeamRating.select(TeamRating.name, TeamRating.elo).where(TeamRating.sport == sport)
            if r.name.strip().lower().startswith(target)
        ]
        if len(candidates) == 1:
            return candidates[0].elo
        return None
    except Exception:
        return None


def compute_and_persist_elo(sport: str, games: list[dict[str, Any]]) -> dict[str, float]:
    """Computes Elo chronologically over `games` (sorted here, caller
    doesn't need to pre-sort) starting from each team's CURRENT rating in
    TeamRating (default `elo_engine.DEFAULT_RATING` for a team never seen
    before), persists the final ratings (upsert, same pattern as
    APIs/football_ratings.py -- TeamRating is current-state, not a time
    series, see that module's docstring for the production bug that
    taught us this) and every individual game (deduped, TeamMatchHistory).

    Fail-closed: DB failures are swallowed (never break the caller's data
    path), same pattern as every other *_writer.py in this project.

    Returns: dict of team_name -> final computed rating (in-memory,
    regardless of whether the DB write succeeded -- callers that just
    want ratings for immediate use, e.g. a fair-price calculation, don't
    need to re-read the DB).
    """
    if not games:
        return {}

    games_sorted = sorted(games, key=lambda g: g["date"])

    try:
        init_db_historical()
        existing = {
            r.name: r.elo
            for r in TeamRating.select(TeamRating.name, TeamRating.elo).where(TeamRating.sport == sport)
        }
    except Exception:
        existing = {}

    ratings = dict(existing)
    match_rows: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)

    for game in games_sorted:
        team_a, team_b = game["team_a"], game["team_b"]
        a_won = game["a_won"]
        rating_a = ratings.get(team_a, elo_engine.DEFAULT_RATING)
        rating_b = ratings.get(team_b, elo_engine.DEFAULT_RATING)
        new_a, new_b = elo_engine.update_ratings(rating_a, rating_b, a_won)
        ratings[team_a] = new_a
        ratings[team_b] = new_b

        match_rows.append({
            "team_name": team_a, "sport": sport, "opponent_name": team_b,
            "match_date": game["date"], "result": "W" if a_won else "L",
            "score": game.get("score"), "source": game.get("source", sport), "fetched_at": now,
        })
        match_rows.append({
            "team_name": team_b, "sport": sport, "opponent_name": team_a,
            "match_date": game["date"], "result": "L" if a_won else "W",
            "score": game.get("score"), "source": game.get("source", sport), "fetched_at": now,
        })

    try:
        _persist_ratings(sport, ratings, now)
        _persist_match_history(match_rows)
    except Exception:
        pass

    return ratings


def _persist_ratings(sport: str, ratings: dict[str, float], now: datetime) -> None:
    rows = [
        {
            "created_at": now,
            "sport": sport,
            "name": name,
            "name_norm": name.strip().lower(),
            "league": sport,
            "elo": elo,
            "n_games": 0,  # not tracked by this simple engine yet (TODO if needed later)
            "source": "kairos_elo_engine",
            "updated_at": now.isoformat(),
            "extra_json": "{}",
        }
        for name, elo in ratings.items()
    ]
    if not rows:
        return
    (
        TeamRating.insert_many(rows)
        .on_conflict(
            conflict_target=[TeamRating.sport, TeamRating.name_norm],
            update={
                TeamRating.name: EXCLUDED.name,
                TeamRating.league: EXCLUDED.league,
                TeamRating.elo: EXCLUDED.elo,
                TeamRating.updated_at: EXCLUDED.updated_at,
                TeamRating.source: EXCLUDED.source,
            },
        )
        .execute()
    )


def _persist_match_history(match_rows: list[dict[str, Any]]) -> None:
    """Deduped by (team_name, sport, opponent_name, match_date) -- same
    rationale as TennisMatchHistory: a real game doesn't change once
    played, re-inserting on every recompute would inflate the table."""
    for row in match_rows:
        exists = (
            TeamMatchHistory
            .select()
            .where(
                (TeamMatchHistory.team_name == row["team_name"])
                & (TeamMatchHistory.sport == row["sport"])
                & (TeamMatchHistory.opponent_name == row["opponent_name"])
                & (TeamMatchHistory.match_date == row["match_date"])
            )
            .exists()
        )
        if exists:
            continue
        TeamMatchHistory.create(**row)
