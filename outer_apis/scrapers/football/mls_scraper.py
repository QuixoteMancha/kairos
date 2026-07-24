"""
MLS match-result scraper — ESPN's public hidden scoreboard API
(site.api.espn.com), no API key/auth required.

Verified live (2026-07-21):
  GET https://site.api.espn.com/apis/site/v2/sports/soccer/usa.1/scoreboard
      ?dates=YYYYMMDD-YYYYMMDD&limit=1000
returns real completed MLS games with team names + final scores for the
given date range (limit=1000 needed -- the endpoint silently caps at 100
events without it, confirmed: a full-year query returned exactly 100
events without `limit`, 544 with it).

This module ONLY does raw HTTP + JSON parsing (no team-name translation,
no Elo, no persistence) -- same separation-of-concerns as
`outer_apis/scrapers/football/clubelo_scraper.py` and
`outer_apis/scrapers/hockey/nhl_stats_scraper.py`. Team-name translation
(ESPN's names aren't prefix-compatible with Kalshi's short names, e.g.
"LA Galaxy" vs Kalshi's "Los Angeles G") lives in
`outer_apis/scrapers/football/mls_teams.py`; Elo computation + persistence
lives in `outer_apis/mls_ratings.py`.
"""

from datetime import date, timedelta
from typing import Any

import requests

_BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/usa.1/scoreboard"
_REQUEST_TIMEOUT_S = 30
_MAX_EVENTS_PER_CALL = 1000


def get_completed_games(days_back: int = 365) -> list[dict[str, Any]]:
    """Real completed MLS games from `days_back` days ago through today.

    Returns:
        List of {"date" (ISO string), "home_team", "away_team",
        "home_score", "away_score"} dicts, one per COMPLETED game only
        (in-progress/scheduled games are skipped). Fail-closed: [] on
        any network error or unparseable response -- never fabricates a
        result.
    """
    end = date.today()
    start = end - timedelta(days=days_back)
    date_range = f"{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}"

    try:
        resp = requests.get(
            _BASE_URL,
            params={"dates": date_range, "limit": _MAX_EVENTS_PER_CALL},
            timeout=_REQUEST_TIMEOUT_S,
        )
    except requests.RequestException:
        return []
    if resp.status_code != 200:
        return []
    try:
        payload = resp.json()
    except ValueError:
        return []

    games: list[dict[str, Any]] = []
    for event in payload.get("events", []):
        competitions = event.get("competitions") or []
        if not competitions:
            continue
        comp = competitions[0]
        status = comp.get("status", {}).get("type", {})
        if not status.get("completed"):
            continue

        home = away = None
        for competitor in comp.get("competitors", []):
            team_name = competitor.get("team", {}).get("displayName")
            score_raw = competitor.get("score")
            if not team_name or score_raw is None:
                continue
            try:
                score = int(float(score_raw))
            except (TypeError, ValueError):
                continue
            if competitor.get("homeAway") == "home":
                home = (team_name, score)
            elif competitor.get("homeAway") == "away":
                away = (team_name, score)

        if not home or not away:
            continue

        games.append({
            "date": event.get("date") or comp.get("date"),
            "home_team": home[0],
            "away_team": away[0],
            "home_score": home[1],
            "away_score": away[1],
        })

    return games
