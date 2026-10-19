"""
Builds "paired" tennis markets (player A vs player B, complementary win
probabilities) from Kalshi's raw ATP/WTA singles-match markets, for
consumption by LimitArb / TennisVModel / TennisFModel.

STRUCTURAL FINDING (verified live 2026-07-20): unlike a generic Kalshi
binary market (one ticker, yes/no complementary within itself), each
ATP/WTA singles match on Kalshi is TWO SEPARATE tickers under the same
`event_ticker` — one per player, each independently phrased "Will
{player} win the {A} vs {B}: {round} match?". Confirmed via the real
`KXATPMATCH`/`KXWTAMATCH` series (e.g. event `KXATPMATCH-26JUL21VALFEL`
splits into tickers `...-VAL` and `...-FEL`). So `current_a_price`/
`current_b_price` for a tennis MATCH are NOT `yes_price`/`1-yes_price` of
a single ticker (that's each player's own yes/no market) — they're the
`yes_price` of EACH of the two paired tickers, which are complementary to
each other only because exactly one player wins the match.

Since these two tickers are complementary same-EVENT markets, buying "yes"
on both simultaneously nets a guaranteed $1 payout if
yes_a_price + yes_b_price + fees < 1.  The generic MarketArb evaluator
handles this cross-ticket shape; it remains read-only/proposal-only.

This module ONLY pairs+enriches market data (grouping raw Kalshi markets,
extracting player names via title regex, looking up rank/tour via
`outer_apis/tennis_rankings.py`) — no arb detection logic, that stays in
strategies/.
"""

import difflib
import re
import unicodedata
from typing import Any

from outer_apis.sports.tennis import rankings as tennis_rankings
from outer_apis.scrapers.tennis.tournament_surface import surface_from_rules_secondary, extract_tournament_name

_RANKING_LOOKUP_DEPTH = 500
_FUZZY_MATCH_CUTOFF = 0.85

# Kalshi series tickers for ATP/WTA regular-tour singles matches (verified
# live 2026-07-20). `KXTENNISGRANDSLAM` also exists (best_of=5 for men's
# slam matches) but had zero open markets at verification time — its
# title/ticker schema could NOT be confirmed live, so it's intentionally
# NOT wired here. TODO: verify and wire when a Grand Slam is in progress.
_SERIES_TOUR = {
    "KXATPMATCH": "ATP",
    "KXWTAMATCH": "WTA",
}

# Regular ATP/WTA tour-level matches are best-of-3 (confirmed: Grand Slam
# men's matches, which are best-of-5, live under the separate
# KXTENNISGRANDSLAM series above, not KXATPMATCH).
_BEST_OF = 3

_TITLE_RE = re.compile(r"^Will (.+?) win the .+ match\?$")
# Ronda del torneo (2026-08-01, pedido explícito del usuario: backtest de
# "en semifinales/finales el underdog infravalorado converge más" --
# necesita filtrar por ronda). El título real confirmado en producción:
# "Will {player} win the {A} vs {B}: {round} match?" -- ej. "... :
# Semifinal match?". `rules_secondary` también la repite ("... Washington
# Semifinal"), se usa como fallback si el título no matchea.
_ROUND_RE = re.compile(r":\s*([\w\s]+?)\s*match\?\s*$")
_ROUND_FALLBACK_RE = re.compile(
    r"\b(Final|Semifinal|Quarterfinal|Round of \d+)\b", re.IGNORECASE,
)


def _extract_player_name(raw_market: dict) -> str | None:
    """Player name for a single-leg ticker, from `yes_sub_title` (short,
    already just the player's name) with a title-regex fallback.
    Fail-closed: None if neither works."""
    sub_title = raw_market.get("yes_sub_title")
    if sub_title:
        return sub_title.strip()
    title = raw_market.get("title") or ""
    m = _TITLE_RE.match(title.strip())
    return m.group(1).strip() if m else None


def _extract_round(raw_market: dict) -> str | None:
    """Ronda del partido ('Semifinal', 'Final', 'Quarterfinal', 'Round of
    32', etc.), del título real de Kalshi ('Will {player} win the {A} vs
    {B}: {round} match?'), con fallback a `rules_secondary` si el título
    no matchea el formato esperado. Fail-closed: None si ninguno matchea
    -- nunca adivina una ronda."""
    title = (raw_market.get("title") or "").strip()
    m = _ROUND_RE.search(title)
    if m:
        return m.group(1).strip()
    rules_secondary = raw_market.get("rules_secondary") or ""
    m = _ROUND_FALLBACK_RE.search(rules_secondary)
    return m.group(1).strip() if m else None


def build_tennis_markets(raw_markets: list[dict]) -> list[dict[str, Any]]:
    """Pairs per-player legs into combined tennis markets.

    Args:
        raw_markets: normalized markets from `outer_apis.kalshi.KalshiAPI.get_markets`
            (must include `raw` with `event_ticker`, `title`,
            `yes_sub_title`) for the `KXATPMATCH`/`KXWTAMATCH` series.

    Returns:
        List of dicts matching the schema TennisVModel/TennisFModel/LimitArb
        expect: {id, type, current_a_price, current_b_price,
        player_a_rank, player_b_rank, tour, best_of, close_time}.

        Fail-closed at every step:
        - an event with != 2 legs (data glitch, or a leg missing due to
          pagination/status filtering) is SKIPPED, not guessed.
        - a leg whose player name can't be extracted is skipped (skips
          the whole event, since we need both).
        - a player whose current rank can't be resolved (not in the
          fetched top-N pool) still produces a market, but with that
          side's rank as None — TennisVModel will then correctly
          fail-closed to 0.0 volatility for that specific market, which
          is the right behavior (no rank data -> no fabricated volatility).
    """
    by_event: dict[str, list[dict]] = {}
    for market in raw_markets:
        raw = market.get("raw", {})
        event_ticker = raw.get("event_ticker")
        series = _series_for_event(event_ticker)
        if series is None or event_ticker is None:
            continue
        by_event.setdefault(event_ticker, []).append(market)

    # Fetch each tour's ranking pool ONCE (cached), not per player.
    name_to_rank_by_tour = {
        tour: _build_name_to_rank(tour) for tour in set(_SERIES_TOUR.values())
    }

    result: list[dict[str, Any]] = []
    for event_ticker, legs in by_event.items():
        if len(legs) != 2:
            continue  # fail-closed: only pair complete 2-leg events.

        series = _series_for_event(event_ticker)
        tour = _SERIES_TOUR[series]

        names = [_extract_player_name(leg["raw"]) for leg in legs]
        if not all(names):
            continue  # fail-closed: can't pair without both names.

        name_to_rank = name_to_rank_by_tour[tour]
        rank_a = _lookup_rank(names[0], name_to_rank)
        rank_b = _lookup_rank(names[1], name_to_rank)

        # Superficie (2026-07-30): extraída de rules_secondary vía tabla
        # estática torneo->superficie, ver outer_apis/scrapers/tennis/
        # tournament_surface.py. Fail-closed: None si el torneo no está en
        # la tabla -- TennisFModel debe tratarlo como "desconocida", nunca
        # asumir una superficie por default.
        rules_secondary = legs[0]["raw"].get("rules_secondary")
        surface = surface_from_rules_secondary(rules_secondary)
        # Nombre de torneo (2026-08-01, pedido explícito del usuario --
        # necesario para el clima real del partido, ver
        # outer_apis/sports/tennis/match_weather.py). Mismo extractor que
        # ya usa surface_from_rules_secondary internamente.
        tournament = extract_tournament_name(rules_secondary)
        round_name = _extract_round(legs[0]["raw"])

        result.append({
            "id": event_ticker,
            "type": "tennis",
            "series_ticker": series,
            "tournament": tournament,
            "round": round_name,
            "current_a_price": legs[0]["current_a_price"],
            "current_b_price": legs[1]["current_a_price"],
            # Cross-ticket: each leg is its own Kalshi market/ticker (see
            # module docstring) — MarketArb needs these to know where to
            # place each leg of a same-event cross-ticket arb, distinct
            # from a same-ticket yes/no arb where both legs share one id.
            "ticker_a": legs[0].get("ticker", legs[0].get("id")),
            "ticker_b": legs[1].get("ticker", legs[1].get("id")),
            "player_a_rank": rank_a,
            "player_b_rank": rank_b,
            "player_a_name": names[0],
            "player_b_name": names[1],
            "tour": tour,
            "surface": surface,
            "best_of": _BEST_OF,
            "close_time": legs[0].get("close_time"),
        })

    return result


def _norm(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9 ]", " ", ascii_str.lower()).strip()


def _build_name_to_rank(tour: str) -> dict[str, int]:
    """Fetches the top-N rankings ONCE per tour (cached by
    `tennis_rankings`'s own TTL cache) and returns a normalized-name ->
    rank dict, instead of a separate HTTP round-trip per player — pairing
    a full slate of tennis markets can involve dozens of players, and a
    per-player lookup (confirmed during this build: it works, but is slow
    enough to be impractical) is the same anti-pattern already fixed once
    in `outer_apis/tennis_serve_stats.py`."""
    players = tennis_rankings.get_top_rankings(n=_RANKING_LOOKUP_DEPTH, tour=tour)
    return {_norm(p["nombre"]): p["ranking"] for p in players if p.get("nombre") and p.get("ranking")}


def _lookup_rank(name: str, name_to_rank: dict[str, int]) -> int | None:
    """Fail-closed: no match above `_FUZZY_MATCH_CUTOFF` -> None (player
    outside the fetched top-N pool, e.g. a challenger-level player)."""
    target = _norm(name)
    if target in name_to_rank:
        return name_to_rank[target]
    matches = difflib.get_close_matches(target, list(name_to_rank.keys()), n=1, cutoff=_FUZZY_MATCH_CUTOFF)
    return name_to_rank[matches[0]] if matches else None


def _series_for_event(event_ticker: str | None) -> str | None:
    if not event_ticker:
        return None
    for series in _SERIES_TOUR:
        if event_ticker.startswith(series):
            return series
    return None
