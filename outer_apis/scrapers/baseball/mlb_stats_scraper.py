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
        List of {"date", "home_team", "away_team", "home_won": bool,
        "game_pk", "game_datetime_utc"}. `game_datetime_utc` (2026-08-05,
        pedido explícito del usuario -- clima para MLB) es el `gameDate`
        REAL de la fuente (primer lanzamiento, UTC, ISO 8601) -- a
        diferencia de tenis (que nunca tuvo hora real de inicio y usa
        un snapshot fijo aproximado), acá SÍ hay hora exacta real, no
        hace falta aproximar nada. Fail-closed: [] on any failure; a
        game not yet Final is skipped (never guesses an in-progress/
        scheduled game's outcome).
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
                "game_pk": game.get("gamePk"),
                "game_datetime_utc": game.get("gameDate"),
            })
    return out


_LIVE_FEED_URL = "https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"


def get_linescore(game_pk: int) -> dict[str, Any] | None:
    """Carreras REALES por entrada de un partido ya terminado -- 2026-08-04,
    pedido explícito del usuario ("la probabilidad de remontadas en MLB
    es baja... investigar fuentes de datos", arrancando por Retrosheet
    pero la propia API oficial de MLB ya lo da sin scraping).

    Returns: {"innings": [{"num": int, "home_runs": int|None,
    "away_runs": int|None}, ...], "home_runs_total": int,
    "away_runs_total": int} -- fail-closed: None si la request falla o
    el partido no tiene linescore real (no debería pasar para un
    partido ya 'Final', pero nunca se fabrica)."""
    try:
        resp = requests.get(_LIVE_FEED_URL.format(game_pk=game_pk), timeout=_REQUEST_TIMEOUT_S)
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None

    linescore = data.get("liveData", {}).get("linescore")
    if not linescore:
        return None

    innings = []
    for inn in linescore.get("innings", []):
        innings.append({
            "num": inn.get("num"),
            "home_runs": inn.get("home", {}).get("runs"),
            "away_runs": inn.get("away", {}).get("runs"),
        })
    teams = linescore.get("teams", {})
    home_total = teams.get("home", {}).get("runs")
    away_total = teams.get("away", {}).get("runs")
    if not innings or home_total is None or away_total is None:
        return None

    return {"innings": innings, "home_runs_total": home_total, "away_runs_total": away_total}
