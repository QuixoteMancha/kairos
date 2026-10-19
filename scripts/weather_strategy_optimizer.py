"""Offline weather execution scenarios and bounded evolutionary rule search."""
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import math
import random
from statistics import mean, pstdev

from models.fairprice.weather_research import utc
from outer_apis.kalshi.fees import order_fee


@dataclass(frozen=True)
class WeatherGenome:
    min_net_edge: float = .08
    min_lead_hours: float = -18.
    max_lead_hours: float = 36.
    max_disagreement_f: float = 4.
    max_hourly_ramp_f: float = 5.
    max_station_day_dollars: float = 3.


def simulate(rows, genome=None, *, fee_coefficient=.07, slippage=.01, quantity=1,
             initial_capital=100., max_quote_age_seconds=90):
    """Buy at captured ask+slippage, require displayed size, hold to settlement.

    Fee coefficient is an explicit scenario, not inferred from today's fee table.
    Cash is locked until the recorded settlement availability. One entry per
    ticker; high/low and every bracket share a station-day exposure ceiling.
    A snapshot is evidence of displayed liquidity, never proof of a future fill.
    """
    genome = genome or WeatherGenome()
    if (not math.isfinite(slippage) or not 0 <= slippage < 1 or
            not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1 or
            not math.isfinite(initial_capital) or initial_capital <= 0 or
            not math.isfinite(max_quote_age_seconds) or max_quote_age_seconds < 0 or
            order_fee(.5, fee_multiplier=fee_coefficient) is None):
        raise ValueError("invalid_execution_scenario")
    if (not all(math.isfinite(v) for v in asdict(genome).values()) or
            not 0 <= genome.min_net_edge <= 1 or genome.min_lead_hours > genome.max_lead_hours or
            min(genome.max_disagreement_f, genome.max_hourly_ramp_f, genome.max_station_day_dollars) <= 0):
        raise ValueError("invalid_genome")
    cash, exposure, seen, pending, trades = initial_capital, defaultdict(float), set(), [], []
    vetoes = Counter()
    for row in sorted(rows, key=lambda r: (utc(r["as_of"]), r["ticker"], r["quote_evidence_id"])):
        decision = utc(row["as_of"])
        remaining = []
        for trade in pending:
            if utc(trade["settles_at"]) <= decision:
                cash += trade["payout"]
            else:
                remaining.append(trade)
        pending = remaining
        if row["ticker"] in seen:
            vetoes["already_entered"] += 1
            continue
        if (row.get("out_of_distribution") or
                not genome.min_lead_hours <= row["lead_hours"] <= genome.max_lead_hours or
                row["disagreement_f"] > genome.max_disagreement_f or
                row["max_hourly_ramp_f"] > genome.max_hourly_ramp_f):
            vetoes["regime_or_timing"] += 1
            continue
        age = (decision - utc(row["quote_available_at"])).total_seconds()
        if not 0 <= age <= max_quote_age_seconds or utc(row["label_available_at"]) <= decision:
            vetoes["invalid_quote_or_settlement_time"] += 1
            continue
        candidates = []
        for side in ("yes", "no"):
            ask, size = row.get(side + "_ask"), row.get(side + "_size")
            if ask is None or size is None or not math.isfinite(ask) or not math.isfinite(size) or size < quantity:
                continue
            fill = ask + slippage
            if not 0 < fill < 1:
                continue
            fee = order_fee(fill, quantity, fee_multiplier=fee_coefficient)
            cost = quantity * fill + fee
            probability = row["probability_yes"] if side == "yes" else 1-row["probability_yes"]
            if not math.isfinite(probability) or not 0 <= probability <= 1:
                continue
            edge = probability - cost / quantity
            if edge >= genome.min_net_edge:
                candidates.append((edge, side, fill, fee, cost))
        if not candidates:
            vetoes["missing_depth_or_net_edge"] += 1
            continue
        edge, side, fill, fee, cost = max(candidates)
        if exposure[row["group"]] + cost > genome.max_station_day_dollars or cost > cash:
            vetoes["exposure_or_cash"] += 1
            continue
        if row["outcome_yes"] not in (0, 1):
            raise ValueError("invalid_binary_outcome")
        payout = quantity * (row["outcome_yes"] if side == "yes" else 1-row["outcome_yes"])
        trade = dict(ticker=row["ticker"], group=row["group"], station_code=row["station_code"],
            as_of=row["as_of"], settles_at=row["label_available_at"], side=side,
            quantity=quantity, fill_price=fill, fee=fee, cost=cost, payout=payout,
            pnl=payout-cost, net_edge=edge, quote_evidence_id=row["quote_evidence_id"])
        trades.append(trade)
        pending.append(trade)
        cash -= cost
        exposure[row["group"]] += cost
        seen.add(row["ticker"])
    total_cost = sum(t["cost"] for t in trades)
    pnl = sum(t["pnl"] for t in trades)
    by_station = Counter()
    for trade in trades:
        by_station[trade["station_code"]] += trade["cost"]
    equity = peak = initial_capital
    drawdown = 0.
    for trade in sorted(trades, key=lambda t: (utc(t["settles_at"]), t["ticker"])):
        equity += trade["pnl"]
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return dict(research_only=True, execution_assumptions=dict(fee_coefficient=fee_coefficient,
        slippage=slippage, quantity=quantity, initial_capital=initial_capital,
        displayed_depth_required=True, fills_verified=False),
        n_trades=len(trades), station_days=len({t["group"] for t in trades}),
        cost=total_cost, pnl=pnl, roi=pnl/total_cost if total_cost else None,
        realized_drawdown=drawdown,
        max_station_cost_share=max(by_station.values(), default=0.)/total_cost if total_cost else 0.,
        trades=trades, vetoes=dict(vetoes))


def evolve(rows, *, seed=20260914, population_size=24, generations=12,
           minimum_days_per_fold=20, **execution):
    """Two chronological validation blocks; latest third is untouched holdout.

    Predictions must be produced by walk_forward. Calendar-date blocks keep every
    station-day and correlated city's date on the same side of each boundary.
    Validation labels unavailable at holdout start are embargoed from fitness.
    Winner selection never reads holdout performance. No automatic promotion.
    """
    if population_size < 4 or generations < 1 or minimum_days_per_fold < 2:
        raise ValueError("invalid_evolution_configuration")
    if any(r.get("calibrated") is not True or not r.get("latest_training_label") or
           utc(r["latest_training_label"]) >= utc(r["as_of"]) for r in rows):
        raise ValueError("evolution_requires_chronological_calibrated_predictions")
    dates = sorted({r["target_date"] for r in rows})
    if len(dates) < 3 * minimum_days_per_fold:
        return dict(status="insufficient_data", research_only=True,
                    reason="insufficient_chronological_dates", dates=len(dates), eligible_for_review=False)
    first, second = len(dates)//3, 2*len(dates)//3
    holdout_dates = set(dates[second:])
    holdout = [r for r in rows if r["target_date"] in holdout_dates]
    freeze = min(utc(r["as_of"]) for r in holdout)
    partitions = [set(dates[:first]), set(dates[first:second])]
    folds = [[r for r in rows if r["target_date"] in part and utc(r["label_available_at"]) < freeze]
             for part in partitions]
    rng = random.Random(seed)
    ranges = dict(min_net_edge=(.02, .18), min_lead_hours=(-20., 0.), max_lead_hours=(6., 48.),
        max_disagreement_f=(1., 8.), max_hourly_ramp_f=(1., 10.), max_station_day_dollars=(1., 5.))
    def random_genome():
        return WeatherGenome(**{key: rng.uniform(*bounds) for key, bounds in ranges.items()})
    def fitness(genome):
        results = [simulate(fold, genome, **execution) for fold in folds]
        if any(r["station_days"] < minimum_days_per_fold or r["roi"] is None for r in results):
            return -math.inf, results
        rois = [r["roi"] for r in results]
        score = mean(rois) - .5*pstdev(rois)
        score -= .1*mean(r["realized_drawdown"]/max(r["cost"], 1.) for r in results)
        score -= .05*mean(r["max_station_cost_share"] for r in results)
        return score, results
    population = [WeatherGenome()] + [random_genome() for _ in range(population_size-1)]
    best, best_score, best_folds = None, -math.inf, []
    for _ in range(generations):
        scored = [(fitness(g), g) for g in population]
        scored.sort(key=lambda pair: (pair[0][0], tuple(asdict(pair[1]).values())), reverse=True)
        if best is None or scored[0][0][0] > best_score:
            (best_score, best_folds), best = scored[0]
        elites = [pair[1] for pair in scored[:max(2, population_size//5)]]
        population = elites.copy()
        while len(population) < population_size:
            left, right = rng.choice(elites), rng.choice(elites)
            values = {}
            for key, (low, high) in ranges.items():
                value = getattr(rng.choice((left, right)), key)
                if rng.random() < .35:
                    value += rng.gauss(0, (high-low)*.1)
                values[key] = max(low, min(high, value))
            population.append(WeatherGenome(**values))
    holdout_result = simulate(holdout, best, **execution)
    eligible = math.isfinite(best_score) and all(r["roi"] is not None and r["roi"] > 0 for r in best_folds)
    eligible = eligible and holdout_result["station_days"] >= minimum_days_per_fold and (holdout_result["roi"] or 0) > 0
    return dict(status="research_complete", research_only=True, eligible_for_review=bool(eligible),
        automatic_promotion=False, seed=seed, population_size=population_size, generations=generations,
        genome=asdict(best), fitness=best_score if math.isfinite(best_score) else None,
        validation=best_folds, holdout=holdout_result, holdout_start=dates[second],
        rules_frozen_at=freeze.isoformat(), validation_dates=[sorted(p) for p in partitions])
