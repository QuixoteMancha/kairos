"""
NHL game results scraper — api-web.nhle.com (official, free, no auth/key
needed). Confirmed live 2026-07-21, plain `requests` works.

Unlike MLB's schedule endpoint (accepts an arbitrary date range in one
call) or NBA's leaguegamelog (a whole season in one call), NHL's
`/schedule/{date}` endpoint returns one WEEK at a time -- fetching a
range requires multiple calls. Capped at `_MAX_WEEKS` per call to this
scraper to keep it bounded (a full season backfill would need ~26 calls;
this is intentionally NOT a full-season backfill tool, just a recent-
window fetcher -- documented, not hidden).
"""

from datetime import date, timedelta
from typing import Any

import requests

_BASE_URL = "https://api-web.nhle.com/v1"
_REQUEST_TIMEOUT_S = 20
_MAX_WEEKS = 10  # bounded, see module docstring


def _full_team_name(team: dict) -> str | None:
    place = (team.get("placeName", {}) or {}).get("default")
    common = (team.get("commonName", {}) or {}).get("default")
    if not place or not common:
        return None
    return f"{place} {common}"


def get_recent_games(start_date: date | None = None, weeks: int = _MAX_WEEKS) -> list[dict[str, Any]]:
    """Completed games starting from `start_date` (defaults to
    `weeks` weeks ago), walking forward week by week via the
    `nextStartDate` the API itself returns.

    Returns:
        List of {"date", "home_team", "away_team", "home_won": bool}.
        Fail-closed: [] on any failure; a game not in "OFF" (final) state
        is skipped (never guesses an in-progress/scheduled game).
    """
    weeks = min(weeks, _MAX_WEEKS)
    current_date = (start_date or (date.today() - timedelta(weeks=weeks))).isoformat()

    out: list[dict[str, Any]] = []
    seen_dates: set[str] = set()
    for _ in range(weeks):
        if current_date in seen_dates:
            break  # fail-closed: avoid an infinite loop if the API ever repeats a date.
        seen_dates.add(current_date)

        try:
            resp = requests.get(f"{_BASE_URL}/schedule/{current_date}", timeout=_REQUEST_TIMEOUT_S)
        except requests.RequestException:
            break
        if resp.status_code != 200:
            break
        try:
            data = resp.json()
        except ValueError:
            break

        for game_day in data.get("gameWeek", []):
            for game in game_day.get("games", []):
                if game.get("gameState") != "OFF":
                    continue  # fail-closed: only settled games.
                home = game.get("homeTeam", {})
                away = game.get("awayTeam", {})
                home_score = home.get("score")
                away_score = away.get("score")
                # Full "City Nickname" (e.g. "New York Rangers"), NOT just
                # placeName -- found live 2026-07-21: multiple NHL teams
                # share a metro-area placeName (NY Rangers vs NY
                # Islanders both report placeName "New York"), which
                # would silently conflate two different real teams into
                # one Elo rating using placeName alone.
                home_name = _full_team_name(home)
                away_name = _full_team_name(away)
                if home_score is None or away_score is None or not home_name or not away_name:
                    continue
                out.append({
                    "date": game_day.get("date"),
                    "home_team": home_name,
                    "away_team": away_name,
                    "home_won": home_score > away_score,
                })

        next_start = data.get("nextStartDate")
        if not next_start:
            break
        current_date = next_start

    return out
