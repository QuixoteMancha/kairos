"""Reproducible BetOnFavourite variant laboratory on canonical matches.

Two pricing domains are deliberately kept separate:

``market_odds``
    Real pre-match decimal odds from TennisExplorer. Only the simple hold
    variant is supported because historical in-play prices are unavailable.

``model_price``
    Synthetic Markov prices at pre-match and set checkpoints. All eight
    historical set variants are supported, but results are simulations and
    must never be presented as executable historical market returns.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from hashlib import sha256
import json
from typing import Any

from database.db import db_historical, init_db_historical
from database.historical.tennis.models import MatchHistory, Player, TennisHistoricalOdds
from models.volatility.sports.tennis.weighted_elo import combined_probability
from scripts.tennis.bet_on_favourite_backtest import simulate
from scripts.tennis.pricing_and_risk_utils import _simulate_realistic_all_strategies
from scripts.tennis.wta_favorite_surface_winrate_filter_backtest import (
    _self_set1_game_diff, _self_won_set_n,
)


@dataclass(frozen=True)
class FilterPreset:
    selector: str = "weighted_elo"
    min_surface_matches: int = 0
    require_better_surface_wr: bool = False
    min_recent_matches: int = 0
    require_better_recent_form: bool = False
    require_momentum_history: bool = False
    veto_better_underdog_momentum: bool = False
    exclude_grass: bool = False
    min_edge: float | None = None
    max_odds: float | None = None
    surface_lookback_days: int = 365


PRESETS = {
    "baseline": FilterPreset(),
    "surface_elo_selector": FilterPreset(selector="surface_elo"),
    "combined_50_50_selector": FilterPreset(selector="combined_50_50"),
    "surface_52w": FilterPreset(
        min_surface_matches=3, require_better_surface_wr=True,
    ),
    "surface_and_form": FilterPreset(
        min_surface_matches=3, require_better_surface_wr=True,
        min_recent_matches=5, require_better_recent_form=True,
    ),
    "momentum_no_grass": FilterPreset(
        min_surface_matches=3, require_better_surface_wr=True,
        min_recent_matches=5, require_better_recent_form=True,
        require_momentum_history=True, veto_better_underdog_momentum=True,
        exclude_grass=True,
    ),
    "strict_edge": FilterPreset(
        min_surface_matches=3, require_better_surface_wr=True,
        min_recent_matches=5, require_better_recent_form=True,
        require_momentum_history=True, veto_better_underdog_momentum=True,
        exclude_grass=True, min_edge=0.03, max_odds=3.0,
    ),
}


def _slope(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    x_mean = (len(values) - 1) / 2
    y_mean = sum(values) / len(values)
    denominator = sum((index - x_mean) ** 2 for index in range(len(values)))
    return sum(
        (index - x_mean) * (value - y_mean)
        for index, value in enumerate(values)
    ) / denominator


def _favorite_side(match: MatchHistory, selector: str) -> str | None:
    overall = (match.player_a_weighted_elo, match.player_b_weighted_elo)
    surface = (match.player_a_surface_elo, match.player_b_surface_elo)
    if None in (*overall, *surface):
        return None
    if selector == "weighted_elo":
        scores = overall
    elif selector == "surface_elo":
        scores = surface
    elif selector == "combined_50_50":
        probability_a = combined_probability(*overall, *surface)
        scores = (probability_a, 1.0 - probability_a)
    else:
        raise ValueError(f"Unknown favorite selector: {selector}")
    if scores[0] == scores[1]:
        return None
    return "a" if scores[0] > scores[1] else "b"


def _age_on(birthdate: date | None, played: date) -> float | None:
    if birthdate is None:
        return None
    return round((played - birthdate).days / 365.2425, 2)


def collect_cases(preset: FilterPreset) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Build point-in-time cases; state is updated only after each match."""
    init_db_historical()
    surface_results: dict[tuple[int, str], deque[tuple[date, bool]]] = defaultdict(deque)
    recent_results: dict[int, deque[bool]] = defaultdict(lambda: deque(maxlen=10))
    elo_history: dict[int, deque[float]] = defaultdict(lambda: deque(maxlen=10))
    skipped: dict[str, int] = defaultdict(int)
    cases: list[dict[str, Any]] = []

    with db_historical.connection_context():
        players = {
            row.id: (row.canonical_name, row.birthdate)
            for row in Player.select(Player.id, Player.canonical_name, Player.birthdate)
        }
        # Load once: calling ``_odds_for_match`` per match caused tens of
        # thousands of round-trips to PostgreSQL for every preset.
        odds_index = {
            (row.player_name, row.opponent_name, row.match_date): (
                float(row.odds_self), float(row.odds_opponent),
            )
            for row in TennisHistoricalOdds.select().where(
                TennisHistoricalOdds.odds_self.is_null(False)
                & TennisHistoricalOdds.odds_opponent.is_null(False)
            )
            if row.odds_self > 1 and row.odds_opponent > 1
        }
        matches = list(
            MatchHistory.select().where(
                MatchHistory.winner.is_null(False)
                & MatchHistory.player_a.is_null(False)
                & MatchHistory.player_b.is_null(False)
                & MatchHistory.match_date.is_null(False)
                & ~(MatchHistory.match_id.startswith("recentform:"))
            ).order_by(MatchHistory.match_date, MatchHistory.id)
        )
        for match in matches:
            try:
                played = date.fromisoformat(match.match_date)
            except (TypeError, ValueError):
                skipped["bad_date"] += 1
                continue
            surface = (match.surface or "").strip().lower()
            favorite_side = _favorite_side(match, preset.selector)
            names = (players[match.player_a_id][0], players[match.player_b_id][0])
            odds = odds_index.get((names[0], names[1], match.match_date))
            if odds is None:
                reverse = odds_index.get((names[1], names[0], match.match_date))
                odds = (reverse[1], reverse[0]) if reverse else None
            if not surface:
                skipped["no_surface"] += 1
            elif favorite_side is None:
                skipped["no_elo"] += 1
            elif odds is None or min(odds) <= 1:
                skipped["no_odds"] += 1
            else:
                favorite_is_a = favorite_side == "a"
                favorite_id = match.player_a_id if favorite_is_a else match.player_b_id
                underdog_id = match.player_b_id if favorite_is_a else match.player_a_id
                favorite_odds = odds[0] if favorite_is_a else odds[1]
                opponent_odds = odds[1] if favorite_is_a else odds[0]
                favorite_rank = match.player_a_elo_rank if favorite_is_a else match.player_b_elo_rank
                underdog_rank = match.player_b_elo_rank if favorite_is_a else match.player_a_elo_rank
                cutoff = played - timedelta(days=preset.surface_lookback_days)
                favorite_surface = [
                    won for day, won in surface_results[(favorite_id, surface)] if day >= cutoff
                ]
                underdog_surface = [
                    won for day, won in surface_results[(underdog_id, surface)] if day >= cutoff
                ]
                favorite_recent = list(recent_results[favorite_id])
                underdog_recent = list(recent_results[underdog_id])
                favorite_elos = list(elo_history[favorite_id])
                underdog_elos = list(elo_history[underdog_id])
                slope5_favorite = _slope(favorite_elos[-5:]) if len(favorite_elos) >= 5 else None
                slope5_underdog = _slope(underdog_elos[-5:]) if len(underdog_elos) >= 5 else None
                slope10_favorite = _slope(favorite_elos) if len(favorite_elos) >= 10 else None
                slope10_underdog = _slope(underdog_elos) if len(underdog_elos) >= 10 else None
                implied = (1 / favorite_odds, 1 / opponent_odds)
                market_probability = implied[0] / sum(implied)
                raw_probability_a = combined_probability(
                    match.player_a_weighted_elo, match.player_b_weighted_elo,
                    match.player_a_surface_elo, match.player_b_surface_elo,
                )
                model_probability = raw_probability_a if favorite_is_a else 1 - raw_probability_a
                edge = model_probability - market_probability

                reason = None
                if preset.exclude_grass and surface == "grass":
                    reason = "grass"
                elif len(favorite_surface) < preset.min_surface_matches or len(underdog_surface) < preset.min_surface_matches:
                    reason = "surface_sample"
                elif preset.require_better_surface_wr and sum(favorite_surface) / len(favorite_surface) <= sum(underdog_surface) / len(underdog_surface):
                    reason = "surface_wr"
                elif len(favorite_recent) < preset.min_recent_matches or len(underdog_recent) < preset.min_recent_matches:
                    reason = "recent_sample"
                elif preset.require_better_recent_form and sum(favorite_recent) / len(favorite_recent) <= sum(underdog_recent) / len(underdog_recent):
                    reason = "recent_form"
                elif preset.require_momentum_history and None in (slope5_favorite, slope5_underdog, slope10_favorite, slope10_underdog):
                    reason = "momentum_sample"
                elif preset.veto_better_underdog_momentum and (slope5_underdog > slope5_favorite or slope10_underdog > slope10_favorite):
                    reason = "momentum_veto"
                elif preset.max_odds is not None and favorite_odds > preset.max_odds:
                    reason = "max_odds"
                elif preset.min_edge is not None and edge < preset.min_edge:
                    reason = "edge"

                if reason:
                    skipped[reason] += 1
                elif favorite_rank is None or underdog_rank is None:
                    skipped["no_elo_rank"] += 1
                else:
                    self_won_set1 = _self_won_set_n(match.score, 1)
                    self_won_set2 = _self_won_set_n(match.score, 2)
                    set1_diff = _self_set1_game_diff(match.score)
                    player_name, player_birthdate = players[favorite_id]
                    cases.append({
                        "match_id": match.match_id, "date": match.match_date,
                        "tour": match.tour, "surface": surface,
                        "favorite_name": player_name,
                        "favorite_rank": int(favorite_rank),
                        "underdog_rank": int(underdog_rank),
                        "rank_gap": abs(int(underdog_rank) - int(favorite_rank)),
                        "favorite_age": _age_on(player_birthdate, played),
                        "best_of": match.best_of or 3,
                        "hit": match.winner_id == favorite_id,
                        "set1_favorite_win": self_won_set1 if favorite_is_a else (None if self_won_set1 is None else not self_won_set1),
                        "set2_favorite_win": self_won_set2 if favorite_is_a else (None if self_won_set2 is None else not self_won_set2),
                        "set1_diff": set1_diff if favorite_is_a else (None if set1_diff is None else -set1_diff),
                        "favorite_decimal_odds": float(favorite_odds),
                        "market_probability_no_vig": market_probability,
                        "model_probability": model_probability, "model_edge": edge,
                    })

            a_won = match.winner_id == match.player_a_id
            recent_results[match.player_a_id].append(a_won)
            recent_results[match.player_b_id].append(not a_won)
            if surface:
                surface_results[(match.player_a_id, surface)].append((played, a_won))
                surface_results[(match.player_b_id, surface)].append((played, not a_won))
            if match.player_a_weighted_elo is not None:
                elo_history[match.player_a_id].append(float(match.player_a_weighted_elo))
            if match.player_b_weighted_elo is not None:
                elo_history[match.player_b_id].append(float(match.player_b_weighted_elo))
    return cases, dict(skipped)


def dataset_sha256(cases: list[dict[str, Any]]) -> str:
    payload = json.dumps(cases, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode()).hexdigest()


def run_preset(name: str, initial_capital: float = 50.0) -> dict[str, Any]:
    preset = PRESETS[name]
    cases, skipped = collect_cases(preset)
    market_cases = [
        {**case, "favorite_elo_rank": case["favorite_rank"]}
        for case in cases
    ]
    market_by_year = {
        str(year): simulate(
            [case for case in market_cases if int(case["date"][:4]) == year],
            initial_capital,
        )
        for year in sorted({int(case["date"][:4]) for case in market_cases})
    }
    model_results = {}
    for tour in ("ATP", "WTA"):
        tour_cases = [case for case in cases if case["tour"] == tour]
        model_results[tour] = _simulate_realistic_all_strategies(
            tour_cases, initial_capital=initial_capital, stake_pct=0.01,
            max_simultaneous_pct=0.5, max_stake_usd=60_000.0, tour=tour,
        )
    return {
        "preset": name, "filters": asdict(preset),
        "dataset_sha256": dataset_sha256(cases), "n_cases": len(cases),
        "date_range": [cases[0]["date"], cases[-1]["date"]] if cases else None,
        "skipped": skipped,
        "market_odds": {
            "supported_variants": ["1_simple_bet_favorite"],
            "unsupported_reason": "No historical in-play market odds after set 1/2.",
            "1_simple_bet_favorite": simulate(market_cases, initial_capital),
            "simple_by_year": market_by_year,
        },
        "model_price": {
            "warning": "Synthetic Markov prices; not historical executable odds.",
            "by_tour": model_results,
        },
    }


def run_lab(initial_capital: float = 50.0) -> dict[str, Any]:
    return {
        "strategy_family": "BetOnFavourite variants",
        "initial_capital": initial_capital, "stake_pct": 0.01,
        "results": {name: run_preset(name, initial_capital) for name in PRESETS},
    }


if __name__ == "__main__":
    print(json.dumps(run_lab(), indent=2, ensure_ascii=False))
