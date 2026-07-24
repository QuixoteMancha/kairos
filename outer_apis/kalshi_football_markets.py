"""
Builds simplified BINARY football/soccer markets from Kalshi's raw
3-way MLS/UEFA markets, for consumption by LimitArb/SoccerFModel.

DESIGN DECISION (confirmed with user 2026-07-21): unlike tennis, football
markets on Kalshi (KXMLSGAME, KXUEFAGAME) are 3-way per event (team A /
team B / tie -- 3 separate tickers, each a complete same-ticket yes/no
binary on its own, e.g. "Will San Jose win?"). Rather than building a
genuine 3-way fair-price/arb model, football is reduced to the SAME
binary yes/no pattern already used everywhere else: identify the
FAVORITE (highest yes price among the team legs, excluding the tie leg)
and treat ONLY that leg's own yes/no as the market -- "yes" = favorite
wins, "no" = favorite doesn't win (correctly covers BOTH "other team
wins" AND "tie", since that's the real complement of a single team's win
probability). The tie leg and the underdog's own leg are NOT used here.

This applies to BOTH LimitArb and MarketArb -- but MarketArb needs NO
special wiring: each leg (including the favorite's) already flows through
the generic /markets scan as its own same-ticket binary market and gets
evaluated independently, exactly like any other Kalshi binary market. This
module exists ONLY to build the enriched dict (with real team_a_rating/
team_b_rating from ClubElo) that LimitArb's fair-price comparison needs
-- MarketArb doesn't need enrichment, plain same-ticket arb math is
already correct without knowing anything about ratings.

This module ONLY pairs+enriches market data (grouping raw Kalshi markets,
extracting team names, looking up real Elo via
`outer_apis/football_ratings.py`) — no arb detection logic, that stays in
strategies/.
"""

import re
from typing import Any

from outer_apis import football_ratings, mls_ratings

# Kalshi series tickers for team-vs-team soccer moneyline markets
# (verified live 2026-07-21: KXMLSGAME has real open markets, KXUEFAGAME
# had zero open markets at verification time -- kept in the list since
# the schema should be identical when UEFA competitions are in season,
# but NOT independently confirmed).
_SERIES = ("KXMLSGAME", "KXUEFAGAME")

_TITLE_RE = re.compile(r"^(.+?) vs (.+?) Winner\?$")


def _extract_team_name(raw_market: dict) -> str | None:
    """Team/side name for a single leg, from `yes_sub_title` (already
    just the team name, or "Tie"). Fail-closed: None if missing."""
    sub_title = raw_market.get("yes_sub_title")
    return sub_title.strip() if sub_title else None


def build_football_markets(raw_markets: list[dict]) -> list[dict[str, Any]]:
    """Groups the 3 legs per event, picks the favorite (excluding the tie
    leg), and builds a single-leg binary market dict enriched with real
    ClubElo ratings for both the favorite and its opponent.

    Args:
        raw_markets: normalized markets from `outer_apis.kalshi.KalshiAPI.get_markets`
            for the KXMLSGAME/KXUEFAGAME series.

    Returns:
        List of dicts: {id, type: 'soccer', current_a_price,
        current_b_price, team_a_rating, team_b_rating, favorite_name,
        underdog_name, close_time}. `current_a_price`/`current_b_price`
        are the FAVORITE leg's own yes/no ask prices (same-ticket
        complementary), NOT a cross-ticket pairing.

        Fail-closed at every step: an event with != 3 legs is skipped
        (data glitch/pagination gap); a leg whose team name can't be
        extracted skips the whole event; if no real Elo rating is found
        for either team, the market is still built (TennisFModel's
        pattern: SoccerFModel/_SportFModel already fail-closed to the
        market price when team_a_rating/team_b_rating are missing, see
        models/fairprice/sports/_base.py) -- `team_a_rating`/
        `team_b_rating` are simply left as None rather than guessed.
    """
    by_event: dict[str, list[dict]] = {}
    for market in raw_markets:
        raw = market.get("raw", {})
        event_ticker = raw.get("event_ticker")
        if not event_ticker or not any(event_ticker.startswith(s) for s in _SERIES):
            continue
        by_event.setdefault(event_ticker, []).append(market)

    result: list[dict[str, Any]] = []
    for event_ticker, legs in by_event.items():
        if len(legs) != 3:
            continue  # fail-closed: only pair complete 3-leg (team/team/tie) events.

        team_legs = []
        for leg in legs:
            name = _extract_team_name(leg["raw"])
            if not name:
                continue
            if name.strip().lower() == "tie":
                continue  # never a betting candidate here, see module docstring.
            team_legs.append((name, leg))

        if len(team_legs) != 2:
            continue  # fail-closed: need exactly the two real teams.

        # Favorite = whichever team leg has the higher yes-ask (current_a_price).
        team_legs.sort(key=lambda t: t[1]["current_a_price"], reverse=True)
        (fav_name, fav_leg), (dog_name, _dog_leg) = team_legs

        # ClubElo first (real European coverage), MLS self-computed Elo as a
        # second real source (ClubElo has ZERO MLS coverage -- see
        # outer_apis/mls_ratings.py docstring), None only if BOTH miss.
        fav_elo = football_ratings.get_team_rating(fav_name) or mls_ratings.get_team_rating(fav_name)
        dog_elo = football_ratings.get_team_rating(dog_name) or mls_ratings.get_team_rating(dog_name)

        result.append({
            "id": fav_leg["id"],
            "type": "soccer",
            "current_a_price": fav_leg["current_a_price"],
            "current_b_price": fav_leg["current_b_price"],
            "team_a_rating": fav_elo,
            "team_b_rating": dog_elo,
            "favorite_name": fav_name,
            "underdog_name": dog_name,
            "close_time": fav_leg.get("close_time"),
        })

    return result
