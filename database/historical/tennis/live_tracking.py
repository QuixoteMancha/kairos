"""Persistencia incremental de punto-a-punto y estadísticas live."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from database.db import init_db_historical
from database.historical.tennis.matchstats import orient_stats_to_match_history
from database.historical.tennis.matchstats_parser import typed_columns_from_stats
from database.historical.tennis.models import (
    LivePointSnapshot, LiveStatsSnapshot, Match, ProbabilityTick,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def summarize_points(points: dict[str, Any]) -> dict[str, int | None]:
    sets = [value for key, value in (points or {}).items() if key.startswith("set_")]
    games = [game for section in sets for game in (section.get("games") or [])]
    return {
        "current_set": len(sets) or None,
        "games_observed": len(games),
        "points_observed": sum(len(game.get("points") or []) for game in games),
        "break_points_observed": sum(len(game.get("break_points") or []) for game in games),
    }


def persist_live_snapshots(
    scanned_match: Match, points: dict[str, Any] | None,
    stats: dict[str, Any] | None,
) -> dict[str, bool]:
    """Guarda como máximo un corte de cada tipo por tick de mercado."""
    init_db_historical()
    tick = (ProbabilityTick.select().where(ProbabilityTick.match == scanned_match)
            .order_by(ProbabilityTick.observed_at.desc(), ProbabilityTick.id.desc()).first())
    if tick is None or not scanned_match.flashscore_match_id \
            or scanned_match.player_a_is_flashscore_p1 is None:
        return {"points": False, "stats": False}

    common = {
        "match": scanned_match,
        "match_history": scanned_match.match_history_id,
        "probability_tick": tick,
        "flashscore_match_id": scanned_match.flashscore_match_id,
        "player_a_is_flashscore_p1": scanned_match.player_a_is_flashscore_p1,
        "observed_at": _now(),
    }
    saved_points = saved_stats = False
    if points:
        _, saved_points = LivePointSnapshot.get_or_create(
            match=scanned_match, probability_tick=tick,
            defaults={
                **common, **summarize_points(points),
                "raw_point_json": json.dumps(points),
            },
        )
    if stats:
        oriented = orient_stats_to_match_history(
            stats, scanned_match.player_a_is_flashscore_p1,
        )
        _, saved_stats = LiveStatsSnapshot.get_or_create(
            match=scanned_match, probability_tick=tick,
            defaults={
                **common,
                "raw_stats_json": json.dumps(oriented),
                **typed_columns_from_stats(oriented.get("match") or {}),
            },
        )
    return {"points": saved_points, "stats": saved_stats}


def latest_live_state(market_id: str) -> dict[str, Any] | None:
    """Último estado consolidado para estrategias/API, sin requests externos."""
    init_db_historical()
    match = Match.get_or_none(Match.market_id == market_id)
    if match is None:
        return None
    point = (LivePointSnapshot.select().where(LivePointSnapshot.match == match)
             .order_by(LivePointSnapshot.observed_at.desc()).first())
    stats = (LiveStatsSnapshot.select().where(LiveStatsSnapshot.match == match)
             .order_by(LiveStatsSnapshot.observed_at.desc()).first())
    tick = (ProbabilityTick.select().where(ProbabilityTick.match == match)
            .order_by(ProbabilityTick.observed_at.desc()).first())
    return {"match": match, "point": point, "stats": stats, "tick": tick}
