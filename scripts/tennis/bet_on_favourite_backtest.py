"""Backtest canónico de :class:`BetOnFavourite` con datos point-in-time.

El favorito se determina por la cuota ofrecida, mientras que el filtro de
ranking usa `player_*_elo_rank`, calculado exclusivamente con partidos
anteriores. El winrate por superficie también se actualiza después de cada
partido. Nunca se usa ranking histórico oficial.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import date, timedelta
from hashlib import sha256
import json
from typing import Any

from database.db import db_historical, init_db_historical
from database.historical.tennis import MatchHistory, TennisHistoricalOdds
from scripts.reports import risk_metrics
from models.volatility.sports.tennis.weighted_elo import (
    calibrate, combined_probability, fit_platt,
)


RANK_GAP_BY_TOUR = {"ATP": 20, "WTA": 30}
MIN_MODEL_EDGE = 0.03


def _odds_for_match(row: MatchHistory) -> tuple[float, float] | None:
    names = (row.player_a.canonical_name, row.player_b.canonical_name)
    linked = TennisHistoricalOdds.get_or_none(TennisHistoricalOdds.match_history == row.id)
    if linked and linked.odds_self and linked.odds_opponent:
        if linked.player_name == names[0]:
            return float(linked.odds_self), float(linked.odds_opponent)
        return float(linked.odds_opponent), float(linked.odds_self)
    direct = TennisHistoricalOdds.get_or_none(
        (TennisHistoricalOdds.player_name == names[0])
        & (TennisHistoricalOdds.opponent_name == names[1])
        & (TennisHistoricalOdds.match_date == row.match_date)
    )
    if direct and direct.odds_self and direct.odds_opponent:
        return float(direct.odds_self), float(direct.odds_opponent)
    reverse = TennisHistoricalOdds.get_or_none(
        (TennisHistoricalOdds.player_name == names[1])
        & (TennisHistoricalOdds.opponent_name == names[0])
        & (TennisHistoricalOdds.match_date == row.match_date)
    )
    if reverse and reverse.odds_self and reverse.odds_opponent:
        return float(reverse.odds_opponent), float(reverse.odds_self)
    return None


def collect_cases() -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Construye una señal por partido, sin leakage de resultado futuro."""
    init_db_historical()
    surface_record: dict[tuple[str, str], list[tuple[date, bool]]] = defaultdict(list)
    recent_record: dict[str, deque[bool]] = defaultdict(lambda: deque(maxlen=10))
    cases: list[dict[str, Any]] = []
    skipped = defaultdict(int)
    with db_historical.connection_context():
        rows = list(
            MatchHistory.select()
            .where(
                MatchHistory.tour.in_(tuple(RANK_GAP_BY_TOUR))
                & MatchHistory.winner.is_null(False)
                & MatchHistory.player_a.is_null(False)
                & MatchHistory.player_b.is_null(False)
                & ~(MatchHistory.match_id.startswith("recentform:"))
            )
            .order_by(MatchHistory.match_date, MatchHistory.id)
        )
        for row in rows:
            try:
                played = date.fromisoformat(row.match_date)
            except (TypeError, ValueError):
                skipped["bad_date"] += 1
                continue
            a_name, b_name = row.player_a.canonical_name, row.player_b.canonical_name
            surface = row.surface
            odds = _odds_for_match(row)
            a_rank, b_rank = row.player_a_elo_rank, row.player_b_elo_rank

            if odds is None:
                skipped["no_odds"] += 1
            elif not surface:
                skipped["no_surface"] += 1
            elif a_rank is None or b_rank is None or a_rank == b_rank:
                skipped["no_elo_rank"] += 1
            elif (
                row.player_a_weighted_elo is None or row.player_b_weighted_elo is None
                or row.player_a_surface_elo is None or row.player_b_surface_elo is None
            ):
                skipped["no_weighted_elo"] += 1
            else:
                a_is_favourite = odds[0] < odds[1]
                fav_name, dog_name = (a_name, b_name) if a_is_favourite else (b_name, a_name)
                fav_rank, dog_rank = (a_rank, b_rank) if a_is_favourite else (b_rank, a_rank)
                fav_odds = odds[0] if a_is_favourite else odds[1]
                raw_a_probability = combined_probability(
                    row.player_a_weighted_elo, row.player_b_weighted_elo,
                    row.player_a_surface_elo, row.player_b_surface_elo,
                )
                raw_favourite_probability = raw_a_probability if a_is_favourite else 1.0 - raw_a_probability
                cutoff = played - timedelta(days=365)
                fav_record = [won for day, won in surface_record[(fav_name, surface)] if day >= cutoff]
                dog_record = [won for day, won in surface_record[(dog_name, surface)] if day >= cutoff]
                fav_recent = list(recent_record[fav_name])
                dog_recent = list(recent_record[dog_name])
                if len(fav_record) < 3 or len(dog_record) < 3:
                    skipped["no_surface_52w_sample"] += 1
                elif len(fav_recent) < 5 or len(dog_recent) < 5:
                    skipped["no_recent_form_sample"] += 1
                elif dog_rank - fav_rank < RANK_GAP_BY_TOUR[row.tour]:
                    skipped["rank_gap"] += 1
                else:
                    fav_wr = sum(fav_record) / len(fav_record)
                    dog_wr = sum(dog_record) / len(dog_record)
                    fav_recent_wr = sum(fav_recent) / len(fav_recent)
                    dog_recent_wr = sum(dog_recent) / len(dog_recent)
                    if fav_wr <= dog_wr:
                        skipped["surface_winrate"] += 1
                    elif fav_recent_wr <= dog_recent_wr:
                        skipped["recent_form"] += 1
                    else:
                        cases.append({
                            "match_id": row.match_id,
                            "date": row.match_date,
                            "tour": row.tour,
                            "surface": surface,
                            "favorite": fav_name,
                            "underdog": dog_name,
                            "favorite_elo_rank": fav_rank,
                            "underdog_elo_rank": dog_rank,
                            "favorite_surface_winrate": fav_wr,
                            "underdog_surface_winrate": dog_wr,
                            "favorite_recent_10_winrate": fav_recent_wr,
                            "underdog_recent_10_winrate": dog_recent_wr,
                            "favorite_decimal_odds": fav_odds,
                            "raw_model_probability": raw_favourite_probability,
                            "hit": row.winner_id == (row.player_a_id if a_is_favourite else row.player_b_id),
                        })

            winner = a_name if row.winner_id == row.player_a_id else b_name
            loser = b_name if winner == a_name else a_name
            if surface:
                surface_record[(winner, surface)].append((played, True))
                surface_record[(loser, surface)].append((played, False))
            recent_record[winner].append(True)
            recent_record[loser].append(False)
    return cases, dict(skipped)


def simulate(
    cases: list[dict[str, Any]],
    initial_capital: float = 50.0,
    stake_pct: float = 0.01,
    max_daily_exposure_pct: float = 0.5,
) -> dict[str, Any]:
    capital = initial_capital
    equity = [capital]
    returns = []
    daily_returns = []
    hits = 0
    skipped_daily_cap = 0
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        by_date[case["date"]].append(case)
    for match_date in sorted(by_date):
        start_capital = capital
        pnl = 0.0
        exposure = 0.0
        for case in sorted(
            by_date[match_date],
            key=lambda c: (c.get("favorite_elo_rank", 10**9), c["match_id"]),
        ):
            stake = start_capital * stake_pct
            if exposure + stake > start_capital * max_daily_exposure_pct:
                skipped_daily_cap += 1
                continue
            exposure += stake
            ret = case["favorite_decimal_odds"] - 1.0 if case["hit"] else -1.0
            pnl += stake * ret
            returns.append(ret)
            hits += int(case["hit"])
        capital = max(0.0, capital + pnl)
        daily_returns.append(pnl / start_capital if start_capital > 0 else 0.0)
        equity.append(capital)
    trading_days = len(by_date)
    dates = sorted(by_date)
    elapsed_years = (
        max(1 / 365.25, (date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days / 365.25)
        if dates else 1.0
    )
    tdpy = trading_days / elapsed_years
    max_dd = risk_metrics.max_drawdown_pct(equity)
    cagr = risk_metrics.cagr_pct(equity, initial_capital, tdpy) if trading_days else None
    return {
        "n_trades": len(returns),
        "skipped_daily_cap": skipped_daily_cap,
        "hit_rate": round(hits / len(returns), 4) if returns else None,
        "final_capital": round(capital, 2),
        "roi_pct": round((capital / initial_capital - 1) * 100, 2),
        "max_drawdown_pct": max_dd,
        "annualized_sharpe": risk_metrics.annualized_sharpe(equity, initial_capital, tdpy) if trading_days else None,
        # A losing binary contract always has -100% trade ROI, therefore
        # trade CVaR is usually -100% and is not a portfolio risk measure.
        "trade_cvar_95_pct": risk_metrics.cvar_95_pct(returns),
        "daily_capital_cvar_95_pct": risk_metrics.cvar_95_pct(daily_returns),
        "cagr_pct": cagr,
    }


def _calibrated_value_cases(
    training: list[dict[str, Any]], evaluation: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    coefficients = {}
    for tour in RANK_GAP_BY_TOUR:
        for surface in ("Hard", "Clay", "Grass"):
            observations = []
            for case in training:
                if case["tour"] != tour or not case["surface"].lower().startswith(surface.lower()):
                    continue
                probability, hit = case["raw_model_probability"], case["hit"]
                observations.extend(((probability, hit), (1.0 - probability, not hit)))
            coefficients[(tour, surface)] = fit_platt(observations)
    selected = []
    for case in evaluation:
        surface = next((s for s in ("Hard", "Clay", "Grass") if case["surface"].lower().startswith(s.lower())), None)
        if surface is None:
            continue
        intercept, slope = coefficients[(case["tour"], surface)]
        probability = calibrate(case["raw_model_probability"], intercept, slope)
        market_probability = 1.0 / case["favorite_decimal_odds"]
        edge = probability - market_probability
        if edge >= MIN_MODEL_EDGE:
            selected.append({**case, "model_probability": probability, "model_edge": edge})
    return selected


def run_walk_forward(cases: list[dict[str, Any]], initial_capital: float = 50.0) -> list[dict[str, Any]]:
    """Calibra con años anteriores y aplica el edge solo al año siguiente."""
    years = sorted({int(c["date"][:4]) for c in cases})
    folds = []
    for year in years[1:]:
        train_n = sum(1 for c in cases if int(c["date"][:4]) < year)
        training = [c for c in cases if int(c["date"][:4]) < year]
        candidates = [c for c in cases if int(c["date"][:4]) == year]
        test = _calibrated_value_cases(training, candidates)
        folds.append({
            "test_year": year, "train_n": train_n,
            "candidate_n": len(candidates), "selected_n": len(test),
            **simulate(test, initial_capital),
        })
    return folds


def run_backtest(initial_capital: float = 50.0) -> dict[str, Any]:
    cases, skipped = collect_cases()
    identity = "\n".join(f'{c["match_id"]}:{c["date"]}' for c in cases)
    return {
        "strategy": "BetOnFavourite",
        "ranking_source": "self_elo_rank_point_in_time",
        "pricing_source": "tennisexplorer_decimal_odds",
        "parameters": {
            "rank_gap_by_tour": RANK_GAP_BY_TOUR, "stake_pct": 0.01,
            "surface_elo_weight": 0.5, "min_model_edge": MIN_MODEL_EDGE,
            "surface_winrate_lookback_days": 365,
            "recent_form_matches": 10,
            "min_surface_matches": 3,
            "min_recent_matches": 5,
        },
        "dataset_sha256": sha256(identity.encode()).hexdigest(),
        "date_range": [cases[0]["date"], cases[-1]["date"]] if cases else None,
        "skipped": skipped,
        "full_sample_candidates": simulate(cases, initial_capital),
        "walk_forward": run_walk_forward(cases, initial_capital),
    }


if __name__ == "__main__":
    print(json.dumps(run_backtest(), indent=2, ensure_ascii=False))
