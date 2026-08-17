"""Consultas derivadas sobre el historial canónico de partidos de tenis."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from database.db import init_db_historical
from database.historical.tennis.models import MatchHistory, Player


def _player(player_name: str) -> Player | None:
    init_db_historical()
    return (Player.select().where(Player.canonical_name ** player_name.strip()).first())


def get_player_matches(
    player_name: str,
    *,
    limit: int | None = 10,
    surface: str | None = None,
    since: date | str | None = None,
    before: date | str | None = None,
) -> list[dict[str, Any]]:
    """Devuelve partidos reales, una sola vez, desde ``MatchHistory``."""
    player = _player(player_name)
    if player is None:
        return []
    condition = ((MatchHistory.player_a == player.id) | (MatchHistory.player_b == player.id))
    condition &= ~MatchHistory.match_id.startswith("recentform:")
    if surface:
        condition &= MatchHistory.surface == surface
    if since:
        condition &= MatchHistory.match_date >= str(since)
    if before:
        condition &= MatchHistory.match_date < str(before)
    query = (MatchHistory.select().where(condition)
             .order_by(MatchHistory.match_date.desc(), MatchHistory.id.desc()))
    if limit is not None:
        query = query.limit(limit)

    result: list[dict[str, Any]] = []
    for match in query:
        is_a = match.player_a_id == player.id
        opponent = match.player_b if is_a else match.player_a
        won = match.winner_id == player.id if match.winner_id is not None else None
        tournament = match.tournament.tournament_name if match.tournament_id else None
        duration = None
        if match.duration_id:
            duration = match.duration.total_minutes
        result.append({
            "match_id": match.match_id,
            "date": match.match_date,
            "fecha": match.match_date,
            "match_date": match.match_date,
            "opponent": opponent.canonical_name if opponent else None,
            "oponente": opponent.canonical_name if opponent else None,
            "subject_won": won,
            "result": "W" if won else "L" if won is False else None,
            "resultado": "W" if won else "L" if won is False else None,
            "surface": match.surface,
            "tournament": tournament,
            "round": match.round,
            "score": match.score,
            "duration_minutes": duration,
            "retired_or_walkover": bool(match.retired and match.player_retired_id == player.id),
            "source": match.source,
        })
    return result


def get_recent_form(player_name: str, *, limit: int = 10, min_matches: int = 5,
                    before: date | str | None = None) -> dict[str, Any] | None:
    rows = get_player_matches(player_name, limit=limit, before=before)
    decided = [row for row in rows if row["subject_won"] is not None]
    if len(decided) < min_matches:
        return None
    return {"win_rate": round(sum(row["subject_won"] for row in decided) / len(decided), 4),
            "n_matches": len(decided)}


def get_surface_form(player_name: str, surface: str, *, days: int = 364,
                     min_matches: int = 3, before: date | str | None = None) -> dict[str, Any] | None:
    end = date.fromisoformat(str(before)) if before else date.today()
    rows = get_player_matches(player_name, limit=None, surface=surface,
                              since=end - timedelta(days=days), before=before)
    decided = [row for row in rows if row["subject_won"] is not None]
    if len(decided) < min_matches:
        return None
    return {"win_rate": round(sum(row["subject_won"] for row in decided) / len(decided), 4),
            "n_matches": len(decided)}


def get_head_to_head(player_a: str, player_b: str, *, limit: int | None = None,
                     before: date | str | None = None) -> dict[str, Any] | None:
    a, b = _player(player_a), _player(player_b)
    if a is None or b is None:
        return None
    condition = (((MatchHistory.player_a == a.id) & (MatchHistory.player_b == b.id)) |
                 ((MatchHistory.player_a == b.id) & (MatchHistory.player_b == a.id)))
    condition &= ~MatchHistory.match_id.startswith("recentform:")
    if before:
        condition &= MatchHistory.match_date < str(before)
    query = MatchHistory.select().where(condition).order_by(MatchHistory.match_date.desc())
    if limit is not None:
        query = query.limit(limit)
    rows = list(query)
    if not rows:
        return None
    return {"a_wins": sum(row.winner_id == a.id for row in rows),
            "b_wins": sum(row.winner_id == b.id for row in rows), "n_matches": len(rows)}
