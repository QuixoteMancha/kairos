"""
Jeff Sackmann's Match Charting Project — raw fetch of per-player,
per-match serve stats (`pts` = service points played, `pts_won` = service
points won) for BOTH tours.

Verified live 2026-07-20 (see kairos-tennis-official-rankings-scraper /
kairos-tennis-data-source-jeffsackmann-dead memory): the ORIGINAL
`JeffSackmann/tennis_atp` and `tennis_wta` repos are 404/gone, but
`JeffSackmann/tennis_MatchChartingProject` is alive and actively updated
(pushed 2026-05), and — unlike the old repos — covers BOTH men's (`m`)
and women's (`w`) matches with the same schema, closing the WTA
calibration gap that TML-Database (ATP-only) couldn't.

Data source: `charting-{m,w}-stats-ServeBasics.csv` on the `master`
branch, one row per (match_id, player, row) where `row` is "Total" (whole
match), "1" (1st-serve-only breakdown) or "2" (2nd-serve-only breakdown).
We only use the "Total" rows here — `pts_won / pts` on the Total row is
the player's overall service-points-won fraction for that match, exactly
what `p_serve` calibration needs.

This module ONLY does raw HTTP + CSV parsing (no business logic, no
ranking bucketing, no caching policy) — the public call surface lives in
`outer_apis/tennis_serve_stats.py` per the project's separation: scrapers/ =
raw source access, outer_apis/<facade>.py = call methods used by the rest of
Kairos.
"""

import csv
import io
from typing import Any

import requests

_BASE_URL = "https://raw.githubusercontent.com/JeffSackmann/tennis_MatchChartingProject/master"
_REQUEST_TIMEOUT_S = 30


def _fetch_csv(filename: str) -> list[dict[str, str]] | None:
    """Fail-closed: any network error, non-200, or unparseable CSV -> None.
    Never returns partial/garbage rows as if they were valid."""
    url = f"{_BASE_URL}/{filename}"
    try:
        resp = requests.get(url, timeout=_REQUEST_TIMEOUT_S)
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    try:
        return list(csv.DictReader(io.StringIO(resp.text)))
    except csv.Error:
        return None


def get_serve_basics(tour: str) -> list[dict[str, Any]]:
    """Per-player, per-match TOTAL serve stats for one tour.

    Args:
        tour: "m" (ATP/men's) or "w" (WTA/women's).

    Returns:
        List of {"match_id", "player", "pts", "pts_won"} dicts, one per
        player per match (only the "Total" row, 1st+2nd serve combined).
        Fail-closed: [] if the fetch/parse fails, or if a row's pts/pts_won
        can't be parsed as a valid positive count (never fabricates a
        stat from a malformed row, just skips it).
    """
    if tour not in ("m", "w"):
        return []

    rows = _fetch_csv(f"charting-{tour}-stats-ServeBasics.csv")
    if rows is None:
        return []

    result: list[dict[str, Any]] = []
    for row in rows:
        if row.get("row") != "Total":
            continue
        player = row.get("player")
        match_id = row.get("match_id")
        if not player or not match_id:
            continue
        try:
            pts = int(row.get("pts", ""))
            pts_won = int(row.get("pts_won", ""))
        except (TypeError, ValueError):
            continue
        if pts <= 0 or pts_won < 0 or pts_won > pts:
            continue
        result.append({"match_id": match_id, "player": player, "pts": pts, "pts_won": pts_won})

    return result
