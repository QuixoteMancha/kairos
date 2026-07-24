"""
ClubElo scraper — real football/soccer team Elo ratings.

Public API, no auth, no proxy needed (confirmed live 2026-07-21, see
memory kairos-team-sports-tables-and-research-2026-07-21: initial curl
attempts appeared to hang in this sandbox but eventually returned real
data after ~20 minutes — genuinely slow from this environment, NOT
blocked. Budget generous timeouts, don't assume a hang means failure).

Endpoints (CSV responses, header row: Rank,Club,Country,Level,Elo,From,To):
  api.clubelo.com/{YYYY-MM-DD}  -> full rankings snapshot for that date
  api.clubelo.com/{ClubName}    -> full historical Elo series for one club

This module ONLY does raw HTTP + CSV parsing (no caching, no business
logic) — the public call surface lives in `outer_apis/football_ratings.py`.
"""

import csv
import io
from datetime import date
from typing import Any

import requests

_BASE_URL = "http://api.clubelo.com"
_REQUEST_TIMEOUT_S = 90  # confirmed slow from this sandbox, generous budget intentional


def _fetch_csv(path: str) -> list[dict[str, str]] | None:
    """Fail-closed: any network error, non-200, or unparseable CSV -> None."""
    try:
        resp = requests.get(f"{_BASE_URL}/{path}", timeout=_REQUEST_TIMEOUT_S)
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    try:
        return list(csv.DictReader(io.StringIO(resp.text)))
    except csv.Error:
        return None


def get_current_ratings(on_date: date | None = None) -> list[dict[str, Any]]:
    """Full Elo rankings snapshot for a given date (defaults to today).

    Returns:
        List of {"team_name", "country", "elo", "level"} dicts.
        Fail-closed: [] on any failure, or if a row's Elo can't be parsed
        as a valid float (skips that row, never fabricates a rating).
    """
    target_date = (on_date or date.today()).isoformat()
    rows = _fetch_csv(target_date)
    if rows is None:
        return []

    result: list[dict[str, Any]] = []
    for row in rows:
        team = row.get("Club")
        elo_raw = row.get("Elo")
        if not team or elo_raw is None:
            continue
        try:
            elo = float(elo_raw)
        except (TypeError, ValueError):
            continue
        level_raw = row.get("Level")
        try:
            level = int(level_raw) if level_raw not in (None, "") else None
        except (TypeError, ValueError):
            level = None
        result.append({
            "team_name": team,
            "country": row.get("Country"),
            "elo": elo,
            "level": level,
        })
    return result


def get_team_history(team_name: str) -> list[dict[str, Any]]:
    """Full historical Elo series for one club (ClubElo's per-club
    endpoint uses the team name directly, no id lookup needed).
    Fail-closed: [] on any failure/unknown team."""
    rows = _fetch_csv(team_name)
    if rows is None:
        return []

    result: list[dict[str, Any]] = []
    for row in rows:
        elo_raw = row.get("Elo")
        if elo_raw is None:
            continue
        try:
            elo = float(elo_raw)
        except (TypeError, ValueError):
            continue
        result.append({
            "team_name": row.get("Club") or team_name,
            "country": row.get("Country"),
            "elo": elo,
            "from": row.get("From"),
            "to": row.get("To"),
        })
    return result
