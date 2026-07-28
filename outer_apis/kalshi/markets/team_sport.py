"""
Builds paired team-vs-team markets for the 4 two-way team sports (MLB,
NBA, NHL, NFL — none of which allow a regulation-season draw, unlike
soccer's 3-way structure, see outer_apis/kalshi_football_markets.py for why
soccer needed a different, single-leg approach).

STRUCTURAL FINDING (verified live 2026-07-21): like tennis
(outer_apis/kalshi_tennis_markets.py), each MLB/NFL game on Kalshi (series
KXMLBGAME/KXNFLGAME, confirmed with real open markets; KXNBAGAME/
KXNHLGAME exist but had ZERO open markets at verification time — NBA/NHL
are off-season in July, NOT independently confirmed live but built with
the same structure since Kalshi's game-market pattern is consistent
across these leagues) is TWO separate per-team tickers under one
event_ticker, each "Will {team} win the {A} vs {B} ... game?" — genuinely
complementary to EACH OTHER directly (no third "tie" leg needed, unlike
soccer), so this reuses tennis's CROSS-TICKET pairing pattern, not
football's favorite-only single-leg reduction.

This means these paired markets are valid for BOTH LimitArb (fair-price/
volatility comparison, needs the enriched real-Elo dict) AND MarketArb
(cross-ticket arb: buying "yes" on both teams' tickers nets a guaranteed
payout if underpriced — same mechanism already documented in
strategies/market_arb.py for tennis).

This module ONLY pairs+enriches market data — no arb detection logic,
that stays in strategies/.
"""

from typing import Any

_SERIES_CONFIG = {
    "KXMLBGAME": {"sport_type": "baseball", "rating_module": "outer_apis.sports.baseball.ratings"},
    "KXNBAGAME": {"sport_type": "basketball", "rating_module": "outer_apis.sports.basketball.ratings"},
    "KXNHLGAME": {"sport_type": "hockey", "rating_module": "outer_apis.sports.hockey.ratings"},
    "KXNFLGAME": {"sport_type": "american_football", "rating_module": "outer_apis.sports.american_football.ratings"},
}


def _extract_team_name(raw_market: dict) -> str | None:
    sub_title = raw_market.get("yes_sub_title")
    return sub_title.strip() if sub_title else None


def build_team_sport_markets(raw_markets: list[dict], series: str) -> list[dict[str, Any]]:
    """Pairs per-team legs into combined team-vs-team markets for one
    series (one of KXMLBGAME/KXNBAGAME/KXNHLGAME/KXNFLGAME).

    Returns:
        List of dicts: {id, type, current_a_price, current_b_price,
        team_a_rating, team_b_rating, team_a_name, team_b_name,
        ticker_a, ticker_b, close_time}. `ticker_a`/`ticker_b` let
        MarketArb place each leg on its own real ticket (see
        strategies/market_arb.py's cross-ticket support, added for tennis).

        Fail-closed at every step: an event with != 2 legs is skipped; a
        leg whose team name can't be extracted skips the whole event; a
        team whose real Elo rating isn't found gets `None` for that
        rating (the fair-price model then fails closed to the market
        price, same as tennis/football when a rank/rating is missing).
    """
    config = _SERIES_CONFIG.get(series)
    if config is None:
        return []

    import importlib
    rating_module = importlib.import_module(config["rating_module"])

    by_event: dict[str, list[dict]] = {}
    for market in raw_markets:
        raw = market.get("raw", {})
        event_ticker = raw.get("event_ticker")
        if not event_ticker or not event_ticker.startswith(series):
            continue
        by_event.setdefault(event_ticker, []).append(market)

    result: list[dict[str, Any]] = []
    for event_ticker, legs in by_event.items():
        if len(legs) != 2:
            continue  # fail-closed: only pair complete 2-leg events.

        names = [_extract_team_name(leg["raw"]) for leg in legs]
        if not all(names):
            continue  # fail-closed: can't pair without both names.

        rating_a = rating_module.get_team_rating(names[0])
        rating_b = rating_module.get_team_rating(names[1])

        result.append({
            "id": event_ticker,
            "type": config["sport_type"],
            "current_a_price": legs[0]["current_a_price"],
            "current_b_price": legs[1]["current_a_price"],
            "ticker_a": legs[0].get("ticker", legs[0].get("id")),
            "ticker_b": legs[1].get("ticker", legs[1].get("id")),
            "team_a_rating": rating_a,
            "team_b_rating": rating_b,
            "team_a_name": names[0],
            "team_b_name": names[1],
            "close_time": legs[0].get("close_time"),
        })

    return result
