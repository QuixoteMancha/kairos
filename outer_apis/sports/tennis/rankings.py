"""
Public call surface for ATP/WTA player-ranking data — wraps the raw
scrapers in `outer_apis/scrapers/tennis/` behind a small, tour-agnostic
interface that the rest of Kairos (e.g. the tennis Markov calibration in
`models/volatility/sports/tennis/tennis_markov.py`) is meant to call.

Separation of concerns: `outer_apis/scrapers/tennis/*.py` only does raw HTTP +
HTML/JSON parsing against the ATP/WTA official sites, no caching or
business logic. This file owns:
  - the tour-agnostic public functions,
  - fail-closed error handling at the call-surface level (never lets a
    scraper exception escape to a caller),
  - a very small in-process cache so `get_top_rankings` isn't re-scraped
    on every call within the same process run (no DB/AppConfig exists in
    this rebuilt repo to persist it across restarts — that's a TODO for
    when/if this needs to survive process restarts).

Both scrapers were re-verified live against the real ATP/WTA sites during
this rebuild (2026-07-20) — see the module docstrings in
`outer_apis/scrapers/tennis/atp_scraper.py` and `wta_scraper.py` for what was
confirmed working vs. dead.
"""

import time
from typing import Any

from outer_apis.scrapers.tennis import atp_scraper, wta_scraper
from database.historical.tennis.writer import save_rankings_snapshot

_CACHE_TTL_S = 3600  # 1h — rankings update weekly, no need to re-scrape often.
_rankings_cache: dict[str, tuple[float, list[dict]]] = {}


def get_top_rankings(n: int = 200, tour: str = "both") -> list[dict[str, Any]]:
    """Top-N players from the official ATP and/or WTA sites.

    Args:
        n:    how many players per tour.
        tour: "ATP" | "WTA" | "both".

    Returns:
        List of dicts: {"nombre", "ranking", "ranking_type", "pais"}.
        Fail-closed per tour: if a tour's scraper fails entirely, that
        tour just contributes an empty list (never raises up to the
        caller, never fabricates rankings).
    """
    rt = tour.upper()
    cache_key = f"{rt}:{n}"
    cached = _rankings_cache.get(cache_key)
    if cached and (time.monotonic() - cached[0]) < _CACHE_TTL_S:
        return cached[1]

    result: list[dict[str, Any]] = []

    if rt in ("ATP", "BOTH"):
        try:
            result.extend(atp_scraper.get_top_rankings(n))
        except Exception:
            # fail-closed: a scraper bug/unexpected exception must not take
            # down the whole call, just contributes nothing for this tour.
            pass

    if rt in ("WTA", "BOTH"):
        try:
            result.extend(wta_scraper.get_top_rankings(n))
        except Exception:
            pass

    _rankings_cache[cache_key] = (time.monotonic(), result)

    # Persiste al historial SOLO en fetch fresco (no en cache hits, ver
    # arriba) -- "both" mezcla ATP/WTA en `result`, así que se persiste
    # por tour real de cada jugador (ranking_type), no por el `rt` de
    # entrada. Fail-closed internamente, ver database/tennis_historical_writer.py.
    for row_tour in ({"ATP", "WTA"} if rt == "BOTH" else {rt}):
        rows_for_tour = [p for p in result if p.get("ranking_type") == row_tour]
        if rows_for_tour:
            save_rankings_snapshot(row_tour, rows_for_tour)

    return result


def get_player_ranking(name: str, tour: str = "auto") -> int | None:
    """Current singles ranking for a named player, or None if not found /
    on any failure (fail-closed — never fabricates a ranking, never
    raises up to the caller).

    Args:
        name: full player name (e.g. "Carlos Alcaraz").
        tour: "ATP" | "WTA" | "auto" (tries ATP first, then WTA).
    """
    data = get_player_data(name, tour=tour)
    if data is None:
        return None
    return data.get("ranking")


def get_player_data(name: str, tour: str = "auto") -> dict[str, Any] | None:
    """Full player record (name, ranking, tour, country) for a named
    player, or None if not found / on any failure. See scraper modules
    for exact dict shape per tour."""
    rt = tour.upper() if tour else "AUTO"

    if rt == "ATP":
        return _safe_call(atp_scraper.get_player_data, name)
    if rt == "WTA":
        return _safe_call(wta_scraper.get_player_data, name)

    # AUTO: try ATP first, then WTA.
    result = _safe_call(atp_scraper.get_player_data, name)
    if result:
        return result
    return _safe_call(wta_scraper.get_player_data, name)


def _safe_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception:
        # fail-closed: any unexpected scraper exception -> None, never
        # propagated to callers (e.g. the volatility model computing a
        # market's arb opportunity shouldn't crash on a scraper hiccup).
        return None
