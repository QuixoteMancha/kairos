"""Helpers shared by TennisExplorer-based player identity maintenance.

Only a numeric ``match-detail`` id from a TennisExplorer row is suitable
for live identity verification.  Other sources may use their own ids or
synthetic placeholders which must never be sent to TennisExplorer.
"""

from __future__ import annotations

from database.historical.tennis.models import MatchHistory, Player


def sample_tennisexplorer_match_id(player: Player) -> tuple[str, str] | None:
    """Return a verifiable TennisExplorer match id and the player's side.

    The source check and numeric-id check deliberately reject synthetic
    ``kalshi:``/``recentform:`` rows as well as ids owned by other sources.
    """
    for side, field in (("a", MatchHistory.player_a), ("b", MatchHistory.player_b)):
        rows = (MatchHistory
                .select(MatchHistory.match_id)
                .where(
                    (field == player.id)
                    & (MatchHistory.source == "tennisexplorer")
                    & MatchHistory.match_id.is_null(False)
                ))
        for row in rows:
            if row.match_id.isdigit():
                return row.match_id, side
    return None
