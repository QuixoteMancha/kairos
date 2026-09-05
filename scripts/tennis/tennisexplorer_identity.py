"""Helpers shared by TennisExplorer-based player identity maintenance.

Only a numeric ``match-detail`` id from a TennisExplorer row is suitable
for live identity verification.  Other sources may use their own ids or
synthetic placeholders which must never be sent to TennisExplorer.
"""

from __future__ import annotations

import re
from datetime import date

from database.historical.tennis.matchtracking import resolve_scanned_match_date
from database.historical.tennis.models import Match, MatchHistory, Player
from outer_apis.scrapers.tennis.tennisexplorer_player_scraper import (
    get_next_match, get_player_match_history,
)

_TOURNAMENT_NOISE = frozenset({"atp", "wta", "itf", "men", "women"})
_MAX_TICKER_TO_MATCH_DAYS = 3


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


def _history_row_date(raw_date: str | None, reference_date: str) -> str | None:
    """Convert TE's current-year ``DD.MM.`` history date to ISO."""
    if not raw_date:
        return None
    match = re.fullmatch(r"\s*(\d{1,2})\.(\d{1,2})\.\s*", raw_date)
    if match is None:
        return None
    try:
        return date(
            int(reference_date[:4]), int(match.group(2)), int(match.group(1)),
        ).isoformat()
    except ValueError:
        return None


def _is_ticker_date_window(history_date: str | None, ticker_date: str) -> bool:
    """Whether a TE result is within the observed Kalshi listing window.

    KX*MATCH tickers are published up to three calendar days before the
    scheduled match (not necessarily on the match date). A negative or wider
    gap is never accepted; candidate selection still requires the exact rival
    name and final match-detail identity confirmation below.
    """
    if history_date is None:
        return False
    try:
        delta = (date.fromisoformat(history_date) - date.fromisoformat(ticker_date)).days
    except ValueError:
        return False
    return 0 <= delta <= _MAX_TICKER_TO_MATCH_DAYS


def _sample_from_verified_opponent_history(player: Player) -> tuple[str, None] | None:
    """Recover a synthetic player from the verified rival's TE history.

    Old Kalshi rows can carry the series expiration as ``match_date`` and
    omit a tournament entirely. The ticker is the listing date; TE results
    can be up to three days later. We find exactly one numeric TE history row
    in that narrow forward window on the known rival's verified profile; its
    opponent text must also confirm the unresolved player's name.
    ``get_match_detail`` remains the final identity confirmation performed
    by the caller.
    """
    synthetic_rows = (MatchHistory
                      .select()
                      .where(
                          ((MatchHistory.player_a == player.id) | (MatchHistory.player_b == player.id))
                          & MatchHistory.match_id.startswith("kalshi:")
                      ))
    history_cache: dict[tuple[int, int], list[dict]] = {}
    for synthetic in synthetic_rows:
        scanned = Match.get_or_none(Match.match_history == synthetic.id)
        if scanned is None:
            continue
        ticker_date = resolve_scanned_match_date(scanned.market_id, scanned.close_time)
        if ticker_date is None:
            continue
        opponent = synthetic.player_b if synthetic.player_a_id == player.id else synthetic.player_a
        if (
            opponent is None
            or not opponent.slug
            or _direct_tennisexplorer_match_id(opponent) is None
        ):
            continue
        cache_key = opponent.id, int(ticker_date[:4])
        if cache_key not in history_cache:
            try:
                history_cache[cache_key] = get_player_match_history(
                    opponent.slug, opponent.canonical_name, years=[cache_key[1]],
                )
            except Exception:
                history_cache[cache_key] = []
        candidates = [
            match for match in history_cache[cache_key]
            if str(match.get("match_id") or "").isdigit()
            and _is_ticker_date_window(
                _history_row_date(match.get("date"), ticker_date), ticker_date,
            )
            and _match_text_identifies_pair(
                f"{opponent.canonical_name} - {match.get('opponent') or ''}",
                player.canonical_name,
                opponent.canonical_name,
            )
        ]
        # A missing synthetic tournament is the fact being repaired, not
        # evidence we can fabricate.  A present one must still agree.
        if synthetic.tournament_id:
            candidates = [
                match for match in candidates
                if _same_tournament(
                    synthetic.tournament.tournament_name, match.get("tournament"),
                )
            ]
        if len(candidates) == 1:
            return str(candidates[0]["match_id"]), None
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
    checks an already-stored real match and the verified rival's TE history,
    both with an exact pair/date match. Finally, it can use a *verified
    rival's* next match after validating the Kalshi date, tournament, and
    both abbreviated names. Fallback sides are unknown until the real
    match-detail is fetched, so they return ``None`` as the side marker.
    """
    return (
        _direct_tennisexplorer_match_id(player)
        or _sample_from_existing_verified_match(player)
        or _sample_from_verified_opponent_history(player)
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


def detail_side_for_verified_slug(player: Player, detail: dict) -> str | None:
    """Find a unique detail side by the pre-verified TE slug.

    This is deliberately reserved for the rival-history fallback, whose
    date+rival+abbreviated-name chain already identified the match. It covers
    benign full-name variants such as ``Greet``/``Greetje`` that the strict
    name matcher correctly refuses to equate on its own.
    """
    if not player.slug:
        return None
    sides = [
        side for side in ("a", "b")
        if detail.get(f"player_{side}_slug") == player.slug
    ]
    return sides[0] if len(sides) == 1 else None
