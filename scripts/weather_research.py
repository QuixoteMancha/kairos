"""Weather research CLI: evidence dataset, walk-forward calibration and evolution."""
import argparse
import json
from pathlib import Path

from models.fairprice.weather_calibration import walk_forward
from scripts.weather_dataset import build_dataset
from scripts.weather_strategy_optimizer import evolve, simulate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("dataset", help="Read historical DB; never capture or migrate")
    build.add_argument("--as-of", required=True)
    build.add_argument("--rounding", required=True, choices=("continuous", "nearest_integer"))
    build.add_argument("--limit", type=int)
    build.add_argument("--out", type=Path, required=True)
    experiment = commands.add_parser("evaluate", help="Compare chronological probability and trading scenarios")
    experiment.add_argument("dataset", type=Path)
    experiment.add_argument("--out", type=Path, required=True)
    experiment.add_argument("--minimum-days", type=int, default=20)
    experiment.add_argument("--fee-coefficient", type=float, required=True,
                            help="Explicit assumed quadratic coefficient, e.g. 0.07; not series metadata")
    experiment.add_argument("--slippage", type=float, required=True, help="Assumed USD per contract")
    experiment.add_argument("--evolve", action="store_true")
    experiment.add_argument("--seed", type=int, default=20260914)
    args = parser.parse_args()
    if args.command == "dataset":
        from datetime import datetime
        try:
            timestamp = datetime.fromisoformat(args.as_of)
        except ValueError:
            parser.error("--as-of must be an ISO timestamp")
        if timestamp.tzinfo is None:
            parser.error("--as-of must include an offset")
        result = build_dataset(as_of=args.as_of, rounding=args.rounding, limit=args.limit)
    else:
        dataset = json.loads(args.dataset.read_text())
        if dataset.get("schema_version") != 1 or dataset.get("truncated"):
            parser.error("requires a complete schema_version=1 dataset")
        samples = dataset["samples"]
        base = walk_forward(samples, minimum_days=args.minimum_days)
        regimes = walk_forward(samples, minimum_days=args.minimum_days, regime=True)
        execution = dict(fee_coefficient=args.fee_coefficient, slippage=args.slippage)
        trading = simulate(base["predictions"], **execution)
        result = dict(schema_version=1, research_only=True, dataset_coverage=dataset["coverage"],
            probability_comparison=base, regime_experiment=regimes, trading_scenario=trading,
            navier_stokes_application="Indirect regime/tail hypothesis; not a PDE solver or singularity detector")
        if args.evolve:
            result["evolution"] = evolve(base["predictions"], seed=args.seed,
                                         minimum_days_per_fold=args.minimum_days, **execution)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    summary = result.get("coverage") or dict(
        empirical_brier=result["probability_comparison"]["empirical"]["brier"],
        station_days=result["probability_comparison"]["empirical"]["station_days"],
        simulated_trades=result["trading_scenario"]["n_trades"])
    print(json.dumps(dict(out=str(args.out), **summary)))


if __name__ == "__main__":
    main()
