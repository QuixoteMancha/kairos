"""
NBA game results scraper — stats.nba.com (unofficial but widely used,
no auth/key needed). Confirmed live 2026-07-21: like ATP Tour, needs
`curl_cffi` TLS/HTTP2 impersonation to get past bot-detection (bare
`requests`/`curl` got HTTP:000 connection failures, same class of
problem as outer_apis/scrapers/tennis/atp_scraper.py -- see that module's
docstring for the root-cause investigation, same fix applies here).

`leaguegamelog` returns an ENTIRE season's games in one call (one row
per team per game — two rows share a GAME_ID), much more efficient than
per-date requests.
"""

from typing import Any

from curl_cffi import requests as curl_requests

_BASE_URL = "https://stats.nba.com/stats"
_HEADERS = {
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}
_REQUEST_TIMEOUT_S = 30
_IMPERSONATE = "chrome"


def _get(path: str, params: dict) -> dict | None:
    """Fail-closed: any network error or non-200 -> None."""
    try:
        resp = curl_requests.get(
            f"{_BASE_URL}/{path}", params=params, headers=_HEADERS,
            timeout=_REQUEST_TIMEOUT_S, impersonate=_IMPERSONATE,
        )
    except curl_requests.RequestsError:
        return None
    if resp.status_code != 200:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


def get_season_games(season: str = "2024-25", season_type: str = "Regular Season") -> list[dict[str, Any]]:
    """All completed games for a season, one row per (game, team) pairing.

    Args:
        season: NBA season string, e.g. "2024-25". Defaults to the most
            recently completed regular season as of this rebuild (NBA is
            off-season in July, so "current season" data would be stale
            anyway) -- callers needing a different season should pass it
            explicitly.

    Returns:
        List of {"game_id", "date", "team_name", "result": "W"|"L"}.
        Fail-closed: [] on any failure. Two rows share the same "game_id"
        (one per team) -- callers pair them up to get a full result.
    """
    data = _get("leaguegamelog", {
        "Counter": 1000,
        "Direction": "DESC",
        "LeagueID": "00",
        "PlayerOrTeam": "T",
        "Season": season,
        "SeasonType": season_type,
        "Sorter": "DATE",
    })
    if data is None:
        return []

    try:
        result_set = data["resultSets"][0]
        headers = result_set["headers"]
        rows = result_set["rowSet"]
    except (KeyError, IndexError, TypeError):
        return []

    idx = {h: i for i, h in enumerate(headers)}
    required = ("GAME_ID", "GAME_DATE", "TEAM_NAME", "WL")
    if not all(k in idx for k in required):
        return []

    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            wl = row[idx["WL"]]
            if wl not in ("W", "L"):
                continue
            out.append({
                "game_id": row[idx["GAME_ID"]],
                "date": row[idx["GAME_DATE"]],
                "team_name": row[idx["TEAM_NAME"]],
                "result": wl,
            })
        except (IndexError, TypeError):
            continue
    return out
