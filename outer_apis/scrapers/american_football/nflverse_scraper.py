"""
NFL game results scraper — nflverse-data's `schedules` GitHub release
(free, public, no auth). Confirmed live 2026-07-21: the plain download
URL needs redirect-following (GitHub release assets 302 to a CDN) --
`requests` follows redirects by default, but document it since a bare
`curl` without `-L` returns an empty body silently (not an error status,
just 0 bytes -- caught this during verification, worth flagging so a
future debugging session doesn't waste time on it again).

URL: https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv
Real data confirmed back to 1999, updated regularly (2025 season /
Super Bowl LX result present as of this verification).
"""

import csv
import io
from typing import Any

import requests

_GAMES_CSV_URL = "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv"
_REQUEST_TIMEOUT_S = 30


def get_games(seasons: tuple[str, ...] = ("2024", "2025")) -> list[dict[str, Any]]:
    """Completed regular-season games for the given seasons.

    Returns:
        List of {"date", "home_team", "away_team", "home_won": bool}
        (team names are official NFL abbreviations, e.g. "SEA", "NE" --
        consistent with the naming already used by the rescued
        `nflverse`-sourced TeamRating rows from the old system, no
        remapping needed). Fail-closed: [] on any failure; a game
        without both scores (not yet played) is skipped.
    """
    try:
        resp = requests.get(_GAMES_CSV_URL, timeout=_REQUEST_TIMEOUT_S, allow_redirects=True)
    except requests.RequestException:
        return []
    if resp.status_code != 200:
        return []

    try:
        rows = list(csv.DictReader(io.StringIO(resp.text)))
    except csv.Error:
        return []

    out: list[dict[str, Any]] = []
    for row in rows:
        if row.get("season") not in seasons:
            continue
        if row.get("game_type") != "REG":
            # Regular season only, same scoping rationale as MLB's
            # gameType=="R" filter -- keeps this consistent/simple,
            # playoff games have different stakes/dynamics not modeled here.
            continue
        home, away = row.get("home_team"), row.get("away_team")
        home_score, away_score = row.get("home_score"), row.get("away_score")
        if not home or not away or not home_score or not away_score:
            continue  # not yet played, or malformed row.
        try:
            home_won = float(home_score) > float(away_score)
        except ValueError:
            continue
        out.append({
            "date": row.get("gameday"),
            "home_team": home,
            "away_team": away,
            "home_won": home_won,
        })
    return out
