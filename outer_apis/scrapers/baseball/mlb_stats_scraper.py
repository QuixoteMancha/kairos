"""
MLB game results scraper — statsapi.mlb.com (official, free, no auth/key
needed). Confirmed live 2026-07-21, no special headers/TLS impersonation
required (unlike ATP/NBA), plain `requests` works.
"""

from typing import Any

import requests

_BASE_URL = "https://statsapi.mlb.com/api/v1"
_REQUEST_TIMEOUT_S = 20


def get_games_in_range(start_date: str, end_date: str) -> list[dict[str, Any]]:
    """Completed games in a date range (inclusive), MLB sportId=1 (majors).

    Args:
        start_date, end_date: "YYYY-MM-DD".

    Returns:
        List of {"date", "home_team", "away_team", "home_won": bool}.
        Fail-closed: [] on any failure; a game not yet Final is skipped
        (never guesses an in-progress/scheduled game's outcome).
    """
    try:
        resp = requests.get(
            f"{_BASE_URL}/schedule",
            params={"sportId": 1, "startDate": start_date, "endDate": end_date},
            timeout=_REQUEST_TIMEOUT_S,
        )
    except requests.RequestException:
        return []
    if resp.status_code != 200:
        return []
    try:
        data = resp.json()
    except ValueError:
        return []

    out: list[dict[str, Any]] = []
    for date_block in data.get("dates", []):
        for game in date_block.get("games", []):
            # Only regular-season games ("R"): confirmed live 2026-07-21
            # that other game types (All-Star "A", spring training,
            # postseason exhibition-style entries) report SHORT/inconsistent
            # team names (e.g. "Angels" instead of "Los Angeles Angels", or
            # "National League All-Stars" -- not a real MLB team at all),
            # which would silently split one real team's Elo across two
            # different name strings. Regular-season games always give the
            # full, consistent team name.
            if game.get("gameType") != "R":
                continue
            status = game.get("status", {})
            if status.get("abstractGameState") != "Final":
                continue  # fail-closed: only settled games, never guess an outcome.
            teams = game.get("teams", {})
            home = teams.get("home", {})
            away = teams.get("away", {})
            home_name = home.get("team", {}).get("name")
            away_name = away.get("team", {}).get("name")
            home_won = home.get("isWinner")
            if not home_name or not away_name or home_won is None:
                continue
            out.append({
                "date": date_block.get("date"),
                "home_team": home_name,
                "away_team": away_name,
                "home_won": bool(home_won),
            })
    return out
