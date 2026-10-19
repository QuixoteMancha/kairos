"""Station/horizon residual calibration with chronological, grouped evidence."""
from collections import defaultdict
from datetime import date
import math
from statistics import mean, pstdev

from models.fairprice.weather_research import probability, utc


def season_distance(left, right):
    gap = abs(date.fromisoformat(left).month - date.fromisoformat(right).month)
    return min(gap, 12 - gap)


def horizon_for(lead_hours):
    return ("intraday" if lead_hours <= 0 else "0_12h" if lead_hours <= 12
            else "12_36h" if lead_hours <= 36 else "36h_plus")


def calibrate(sample, history, *, minimum_days=20, lookback_days=730, regime=False):
    """Use each station/day once per kind/horizon; labels must precede decision.

    Returns a smoothed empirical residual mixture plus Gaussian and uncorrected
    baselines. Model weights use inverse training MSE after bias correction. They
    do not assume independent model errors or shrink sigma by sqrt(model count).
    """
    if minimum_days < 2 or lookback_days < 1:
        raise ValueError("invalid calibration configuration")
    cutoff, target = utc(sample["as_of"]), date.fromisoformat(sample["target_date"])
    unique = {}
    for row in history:
        age = (target - date.fromisoformat(row["target_date"])).days
        if (row["station_code"] != sample["station_code"] or row["kind"] != sample["kind"]
                or row["horizon"] != sample["horizon"] or not 0 < age <= lookback_days
                or utc(row["label_available_at"]) >= cutoff or utc(row["as_of"]) >= cutoff
                or row["group"] == sample["group"]):
            continue
        previous = unique.get(row["group"])
        if previous is None or (row["as_of"], row["quote_evidence_id"]) > (previous["as_of"], previous["quote_evidence_id"]):
            unique[row["group"]] = row
    training = sorted(unique.values(), key=lambda row: (row["target_date"], row["group"]))
    seasonal = [r for r in training if season_distance(r["target_date"], sample["target_date"]) <= 1]
    scope = "station_horizon"
    if len(seasonal) >= minimum_days:
        training, scope = seasonal, "station_horizon_season"
    if len(training) < minimum_days:
        return dict(status="abstain", reasons=["insufficient_calibration_days"],
                    training_days=len(training), required_days=minimum_days)
    ramps = sorted(r["max_hourly_ramp_f"] for r in training)
    ramp_threshold = ramps[min(len(ramps) - 1, int(.8 * len(ramps)))]
    selected_regime = "rapid_change" if sample["max_hourly_ramp_f"] > ramp_threshold else "ordinary"
    if regime:
        subset = [r for r in training if (r["max_hourly_ramp_f"] > ramp_threshold) ==
                  (selected_regime == "rapid_change")]
        if len(subset) >= minimum_days:
            training, scope = subset, scope + "_regime"
    residuals = defaultdict(list)
    for row in training:
        for model, center in zip(row["model_names"], row["model_centers_f"]):
            residuals[model].append(row["target_f"] - center)
    components, weights = [], []
    for model, center in zip(sample["model_names"], sample["model_centers_f"]):
        errors = residuals[model]
        if len(errors) < minimum_days:
            continue
        bias, sigma = mean(errors), max(.5, pstdev(errors))
        options = dict(observed_bound=sample.get("observed_bound_f"), kind=sample["kind"])
        empirical = probability(sample["raw_predicate"], [center + error for error in errors],
                                .35, sample["rounding"], **options)
        gaussian = probability(sample["raw_predicate"], [center + bias], sigma,
                               sample["rounding"], **options)
        components.append(dict(model=model, bias_f=bias, sigma_f=sigma,
            training_days=len(errors), empirical_yes=empirical, gaussian_yes=gaussian))
        weights.append(1 / sigma**2)
    if not components:
        return dict(status="abstain", reasons=["insufficient_model_calibration_days"],
                    training_days=len(training), required_days=minimum_days)
    total = sum(weights)
    for component, weight in zip(components, weights):
        component["weight"] = weight / total
    p = sum(c["weight"] * c["empirical_yes"] for c in components)
    gaussian = sum(c["weight"] * c["gaussian_yes"] for c in components)
    raw = probability(sample["raw_predicate"], sample["model_centers_f"], 2.2,
                      sample["rounding"], observed_bound=sample.get("observed_bound_f"), kind=sample["kind"])
    return dict(status="calibrated_research", research_only=True, calibrated=True, probability_yes=p,
        probability_no=1-p, gaussian_probability_yes=gaussian, baseline_probability_yes=raw,
        components=components, training_days=len(training), training_scope=scope,
        training_groups=sorted(r["group"] for r in training),
        latest_training_label=max(r["label_available_at"] for r in training),
        regime=selected_regime, regime_threshold_f=ramp_threshold,
        out_of_distribution=sample["max_hourly_ramp_f"] > max(ramps) * 1.5 + .5)


def metrics(rows, probability_key="probability_yes"):
    """Weight station-days equally; repeated ticks/brackets cannot inflate N."""
    groups = defaultdict(list)
    for row in rows:
        groups[row["group"]].append(row)
    if not groups:
        return dict(station_days=0, samples=0, brier=None, log_loss=None, reliability=[])
    brier, loss, bins = [], [], defaultdict(list)
    for group in groups.values():
        brier.append(mean((r[probability_key] - r["outcome_yes"])**2 for r in group))
        loss.append(mean(-math.log(max(1e-9, min(1-1e-9,
            r[probability_key] if r["outcome_yes"] else 1-r[probability_key]))) for r in group))
        per_bin = defaultdict(list)
        for row in group:
            per_bin[min(9, int(row[probability_key] * 10))].append(row)
        for bucket, members in per_bin.items():
            bins[bucket].append((mean(r[probability_key] for r in members), mean(r["outcome_yes"] for r in members)))
    reliability = [dict(bin=bucket, station_days=len(values), predicted=mean(v[0] for v in values),
                        observed=mean(v[1] for v in values)) for bucket, values in sorted(bins.items())]
    return dict(station_days=len(groups), samples=len(rows), brier=mean(brier),
                log_loss=mean(loss), reliability=reliability)


def walk_forward(samples, *, minimum_days=20, regime=False):
    predictions, abstentions = [], defaultdict(int)
    for row in sorted(samples, key=lambda row: (row["as_of"], row["ticker"], row["quote_evidence_id"])):
        result = calibrate(row, samples, minimum_days=minimum_days, regime=regime)
        if result["status"] == "abstain":
            for reason in result["reasons"]:
                abstentions[reason] += 1
            continue
        predictions.append({**row, **result})
    return dict(predictions=predictions, abstentions=dict(abstentions),
        empirical=metrics(predictions), gaussian=metrics(predictions, "gaussian_probability_yes"),
        uncorrected=metrics(predictions, "baseline_probability_yes"), regime_conditioning=regime)
