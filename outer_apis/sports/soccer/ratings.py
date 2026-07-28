"""
Public call surface for real football/soccer team Elo ratings — wraps
`outer_apis/scrapers/football/clubelo_scraper.py`, same pattern as
`outer_apis/tennis_rankings.py`.

Separation of concerns: the scraper only does raw HTTP + CSV parsing,
this file owns caching, fail-closed error handling, and persistence into
the historical DB (TeamRating, database/models_historical.py — already
multi-sport-capable, see kairos-team-sports-tables-and-research-2026-07-21
memory).
"""

import json
from datetime import datetime, timezone
from typing import Any

from peewee import EXCLUDED

from outer_apis.scrapers.football import clubelo_scraper
from database.db import init_db_historical
from database.historical.team_sports.teamrating import TeamRating
from database.historical.team_sports.writer import get_current_ratings_detailed

_DB_FRESHNESS_HOURS = 6.0  # Elo ratings update weekly-ish (post-matchday), no need to re-scrape often.
SOURCE = "clubelo"
SPORT = "football"


def get_team_ratings(top_n: int | None = None) -> list[dict[str, Any]]:
    """Current full Elo rankings snapshot.

    Args:
        top_n: if set, only the top N teams by Elo are returned (the
            source doesn't paginate, this just slices the response).

    Returns:
        List of {"team_name", "country", "elo", "level"} dicts, sorted by
        Elo descending. Fail-closed: [] on any failure.
    """
    result = get_current_ratings_detailed(SPORT, source=SOURCE, max_age_hours=_DB_FRESHNESS_HOURS)
    if result is None:
        try:
            result = clubelo_scraper.get_current_ratings()
        except Exception:
            result = []
        result.sort(key=lambda t: t["elo"], reverse=True)

        # Persiste al historial SOLO en fetch fresco (no en cache hits).
        if result:
            _save_ratings_snapshot(result)

    return result[:top_n] if top_n else result


def get_team_rating(team_name: str) -> float | None:
    """Current Elo for a named team, or None if not found / on failure.
    Fail-closed: exact-name lookup against the cached/fresh snapshot, no
    fuzzy matching here (ClubElo's own team-name spelling is what's used
    consistently across TeamRating -- callers should use the same
    spelling ClubElo uses, e.g. via get_team_ratings() first)."""
    for team in get_team_ratings():
        if team["team_name"] == team_name:
            return team["elo"]
    return None


def _save_ratings_snapshot(teams: list[dict[str, Any]]) -> None:
    """Fail-closed: a DB write failure must never break the caller's
    actual data path.

    UPSERT, not blind insert: TeamRating is a CURRENT-STATE table, one
    row per (sport, name_norm) -- the real schema enforces this with a
    UNIQUE index (found the hard way, see TeamRating's docstring in
    database/models_historical.py: a first version of this function did
    a blind insert_many and got IntegrityError on every write in
    production, silently swallowed by this same try/except until
    investigated). `elo`/`league`/`updated_at`/`created_at` get
    refreshed on conflict; `n_games`/`source`/`extra_json` are left as
    whatever they already were for a pre-existing row (only set on first
    insert) since this endpoint doesn't provide `n_games` at all."""
    try:
        init_db_historical()
        now = datetime.now(timezone.utc)
        rows = [
            {
                "created_at": now,
                "sport": SPORT,
                "name": t["team_name"],
                "name_norm": t["team_name"].strip().lower(),
                "league": t.get("country") or "",
                "elo": t["elo"],
                "n_games": 0,  # not provided by this endpoint (ClubElo's per-club history endpoint has it, this snapshot endpoint doesn't)
                "source": SOURCE,
                "updated_at": now.isoformat(),
                # level se persiste acá (antes siempre "{}") para que
                # get_current_ratings_detailed() pueda reconstruirlo sin
                # perder el campo al leer de vuelta desde la DB.
                "extra_json": json.dumps({"level": t.get("level")}),
            }
            for t in teams
        ]
        if rows:
            (
                TeamRating.insert_many(rows)
                .on_conflict(
                    conflict_target=[TeamRating.sport, TeamRating.name_norm],
                    update={
                        TeamRating.name: EXCLUDED.name,
                        TeamRating.league: EXCLUDED.league,
                        TeamRating.elo: EXCLUDED.elo,
                        TeamRating.updated_at: EXCLUDED.updated_at,
                        TeamRating.extra_json: EXCLUDED.extra_json,
                    },
                )
                .execute()
            )
    except Exception:
        pass
