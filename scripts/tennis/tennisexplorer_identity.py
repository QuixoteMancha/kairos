"""Helpers shared by TennisExplorer-based player identity maintenance.

Only a numeric ``match-detail`` id from a TennisExplorer row is suitable
for live identity verification.  Other sources may use their own ids or
synthetic placeholders which must never be sent to TennisExplorer.
"""

from __future__ import annotations

import re
from datetime import date

from database.historical.tennis.models import MatchHistory, Player
from outer_apis.scrapers.tennis.tennisexplorer_player_scraper import get_next_match

_TOURNAMENT_NOISE = frozenset({"atp", "wta", "itf", "men", "women"})


def _direct_tennisexplorer_match_id(player: Player) -> tuple[str, str] | None:
    """Return an id already attached to this player, without fallback."""
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


def _tournament_tokens(value: str | None) -> set[str]:
    return {
        token for token in re.findall(r"[a-z0-9]+", (value or "").lower())
        if token not in _TOURNAMENT_NOISE
    }


def _same_tournament(left: str | None, right: str | None) -> bool:
    left_tokens = _tournament_tokens(left)
    right_tokens = _tournament_tokens(right)
    return bool(left_tokens and right_tokens and (
        left_tokens <= right_tokens or right_tokens <= left_tokens
    ))


def _next_start_matches_date(start_raw: str | None, expected_date: str | None) -> bool:
    """Validate TE's ``DD.MM. HH:MM`` next-match date against Kalshi's ISO date."""
    if not start_raw or not expected_date:
        return False
    match = re.search(r"\b(\d{1,2})\.(\d{1,2})\.", start_raw)
    if match is None:
        return False
    try:
        return date(int(expected_date[:4]), int(match.group(2)), int(match.group(1))).isoformat() == expected_date
    except ValueError:
        return False


def _match_text_identifies_pair(match_text: str | None, player_name: str, opponent_name: str) -> bool:
    """Confirm both known players against TE's abbreviated next-match text."""
    if not match_text:
        return False
    sides = [side.strip() for side in re.split(r"\s+-\s+", match_text) if side.strip()]
    if len(sides) != 2:
        return False
    # This is the existing strict surname + given-name/initial comparison;
    # using it here prevents a same-surname opponent from validating a match.
    from database.historical.tennis.writer import _names_confirm_identity

    return (
        _names_confirm_identity(player_name, sides[0])
        and _names_confirm_identity(opponent_name, sides[1])
    ) or (
        _names_confirm_identity(player_name, sides[1])
        and _names_confirm_identity(opponent_name, sides[0])
    )


def _sample_from_existing_verified_match(player: Player) -> tuple[str, None] | None:
    """Recover an old Kalshi-only player from one unambiguous real match.

    This covers an ordering race: Kalshi can create ``Surname I.`` before
    the history worker later creates the full TE identity.  The synthetic
    row is a usable *query* only when date, tournament, and the already
    verified opponent identify exactly one numeric TE match in our own DB.
    """
    synthetic_rows = (MatchHistory
                      .select()
                      .where(
                          ((MatchHistory.player_a == player.id) | (MatchHistory.player_b == player.id))
                          & (MatchHistory.match_id.startswith("kalshi:"))
                          & MatchHistory.match_date.is_null(False)
                          & MatchHistory.tournament.is_null(False)
                      ))
    for synthetic in synthetic_rows:
        opponent = synthetic.player_b if synthetic.player_a_id == player.id else synthetic.player_a
        if opponent is None or _direct_tennisexplorer_match_id(opponent) is None:
            continue
        candidates = []
        rows = (MatchHistory
                .select()
                .where(
                    (MatchHistory.source == "tennisexplorer")
                    & (MatchHistory.match_date == synthetic.match_date)
                    & MatchHistory.tournament.is_null(False)
                    & MatchHistory.match_id.is_null(False)
                ))
        for row in rows:
            if not row.match_id.isdigit() or not _same_tournament(
                row.tournament.tournament_name, synthetic.tournament.tournament_name,
            ):
                continue
            if row.player_a_id == opponent.id and row.player_b_id != player.id:
                candidates.append(row)
            elif row.player_b_id == opponent.id and row.player_a_id != player.id:
                candidates.append(row)
        if len(candidates) == 1:
            return candidates[0].match_id, None
    return None


def _sample_from_verified_opponent_next_match(player: Player) -> tuple[str, None] | None:
    """Use a rival's next match only after validating it against a Kalshi row.

    A synthetic Kalshi row supplies the expected opponent, date, and tournament;
    it never supplies an id to TennisExplorer.  The rival must already have a
    direct TennisExplorer row, and every returned next-match candidate must
    agree on all three pieces of evidence before its real id can be used.
    """
    rows = (MatchHistory
            .select()
            .where((MatchHistory.player_a == player.id) | (MatchHistory.player_b == player.id))
            .order_by(MatchHistory.match_date))
    next_matches: dict[int, dict | None] = {}
    for row in rows:
        opponent = row.player_b if row.player_a_id == player.id else row.player_a
        if opponent is None or opponent.id == player.id or not row.match_date or not row.tournament_id:
            continue
        # This proves the rival has an identity already corroborated by TE;
        # do not use a second unresolved Kalshi-only player as a bridge.
        if _direct_tennisexplorer_match_id(opponent) is None:
            continue
        if opponent.id not in next_matches:
            try:
                next_matches[opponent.id] = get_next_match(opponent.canonical_name)
            except Exception:
                next_matches[opponent.id] = None
        next_match = next_matches[opponent.id]
        if not next_match or not str(next_match.get("match_id") or "").isdigit():
            continue
        if not _next_start_matches_date(next_match.get("start_raw"), row.match_date):
            continue
        if not _same_tournament(
            next_match.get("tournament"), row.tournament.tournament_name,
        ):
            continue
        if _match_text_identifies_pair(
            next_match.get("match_text"), player.canonical_name, opponent.canonical_name,
        ):
            return str(next_match["match_id"]), None
    return None


def sample_tennisexplorer_match_id(player: Player) -> tuple[str, str | None] | None:
    """Return a verifiable TennisExplorer match id and the player's side.

    First prefers a numeric id directly attached to the player. It then
    checks for exactly one already-stored real match matching a synthetic
    row's opponent/date/tournament (the historical ordering-race repair).
    Finally, it can use a *verified rival's* next match, but only after that
    next match agrees with the player's Kalshi row on date, tournament, and
    both abbreviated names. Fallback sides are unknown until the real
    match-detail is fetched, so they return ``None`` as the side marker.
    """
    return (
        _direct_tennisexplorer_match_id(player)
        or _sample_from_existing_verified_match(player)
        or _sample_from_verified_opponent_next_match(player)
    )


def detail_side_for_player(player: Player, detail: dict) -> str | None:
    """Return the unique TE detail side whose full name confirms ``player``."""
    from database.historical.tennis.writer import _names_confirm_identity

    sides = [
        side for side in ("a", "b")
        if detail.get(f"player_{side}_name")
        and _names_confirm_identity(player.canonical_name, detail[f"player_{side}_name"])
    ]
    return sides[0] if len(sides) == 1 else None
