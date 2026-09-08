"""Búsqueda evolutiva reproducible de reglas de entrada para challengers.

El algoritmo sólo optimiza reglas de selección sobre predicciones que ya
eran *out-of-sample*. Los años 2024-2025 son validación y 2026 queda fuera
de la función de fitness: sólo se consulta al final como holdout. No tiene
acceso a APIs de ejecución ni puede promover una estrategia.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from statistics import fmean, pstdev
from typing import Any

import numpy as np

from scripts.tennis.bet_on_favourite_backtest import simulate


# Con tres años disponibles, se usan dos para evolución/validación y el
# último como holdout. Se conservan estos valores como referencia de la
# primera ventana esperada; la función elige años dinámicamente.
VALIDATION_YEARS = (2024, 2025)
HOLDOUT_YEAR = 2026
DEFAULT_POPULATION_SIZE = 36
DEFAULT_GENERATIONS = 18
DEFAULT_MIN_TRADES_PER_FOLD = 25


@dataclass(frozen=True)
class StrategyGenome:
    """Reglas que un challenger puede variar, dentro de límites prudentes."""

    min_edge: float
    min_decimal_odds: float
    max_decimal_odds: float
    min_weighted_elo_diff: float
    min_recent_wr_diff: float

    def as_dict(self) -> dict[str, float]:
        return {key: round(float(value), 6) for key, value in asdict(self).items()}


def select_cases(cases: list[dict[str, Any]], genome: StrategyGenome) -> list[dict[str, Any]]:
    """Aplica una regla sin imputar indicadores que faltan."""
    selected = []
    for case in cases:
        if case.get("statistics_evidence_complete") is not True:
            continue
        probability = case.get("model_probability")
        market = case.get("market_probability")
        elo_diff = case.get("weighted_elo_diff")
        recent_diff = case.get("recent_wr_diff")
        odds = case.get("favorite_decimal_odds")
        if None in (probability, market, elo_diff, recent_diff, odds):
            continue
        edge = float(probability) - float(market)
        if (
            edge >= genome.min_edge
            and genome.min_decimal_odds <= float(odds) <= genome.max_decimal_odds
            and float(elo_diff) >= genome.min_weighted_elo_diff
            and float(recent_diff) >= genome.min_recent_wr_diff
        ):
            selected.append({**case, "model_edge": edge})
    return selected


def _random_genome(rng: np.random.Generator) -> StrategyGenome:
    minimum_odds = float(rng.uniform(1.08, 1.70))
    return StrategyGenome(
        min_edge=float(rng.uniform(0.01, 0.16)),
        min_decimal_odds=minimum_odds,
        max_decimal_odds=float(rng.uniform(max(1.75, minimum_odds + 0.15), 3.0)),
        min_weighted_elo_diff=float(rng.uniform(-25.0, 260.0)),
        min_recent_wr_diff=float(rng.uniform(-0.10, 0.35)),
    )


def _clamp_genome(genome: StrategyGenome) -> StrategyGenome:
    minimum_odds = min(2.85, max(1.01, genome.min_decimal_odds))
    maximum_odds = min(3.0, max(minimum_odds + 0.05, genome.max_decimal_odds))
    return StrategyGenome(
        min_edge=min(0.20, max(0.0, genome.min_edge)),
        min_decimal_odds=minimum_odds,
        max_decimal_odds=maximum_odds,
        min_weighted_elo_diff=min(350.0, max(-100.0, genome.min_weighted_elo_diff)),
        min_recent_wr_diff=min(0.50, max(-0.25, genome.min_recent_wr_diff)),
    )


def _breed(left: StrategyGenome, right: StrategyGenome, rng: np.random.Generator) -> StrategyGenome:
    """Cruza dos reglas y muta suavemente sus umbrales."""
    values = {}
    mutation_scale = {
        "min_edge": 0.015,
        "min_decimal_odds": 0.10,
        "max_decimal_odds": 0.12,
        "min_weighted_elo_diff": 25.0,
        "min_recent_wr_diff": 0.035,
    }
    for field, scale in mutation_scale.items():
        value = getattr(left, field) if rng.random() < 0.5 else getattr(right, field)
        if rng.random() < 0.35:
            value += float(rng.normal(0.0, scale))
        values[field] = value
    return _clamp_genome(StrategyGenome(**values))


def _folds(cases: list[dict[str, Any]], genome: StrategyGenome, years: tuple[int, ...]) -> list[dict[str, Any]]:
    result = []
    for year in years:
        selected = select_cases(
            [case for case in cases if int(case["date"][:4]) == year], genome,
        )
        result.append({"year": year, **simulate(selected)})
    return result


def _fitness(folds: list[dict[str, Any]], minimum_trades: int) -> float:
    """Privilegia ROI medio estable; cobertura insuficiente es inválida."""
    if not folds or any(fold["n_trades"] < minimum_trades for fold in folds):
        return float("-inf")
    rois = [float(fold["roi_pct"]) for fold in folds]
    # Penaliza reglas cuyo rendimiento depende de un único año excepcional.
    return fmean(rois) - 0.35 * pstdev(rois)


def _seed_for_algorithm(algorithm: str, seed: int) -> int:
    digest = sha256(algorithm.encode()).digest()
    return seed + int.from_bytes(digest[:4], "big")


def evolve_strategy_rules(
    predictions_by_algorithm: dict[str, list[dict[str, Any]]],
    *,
    seed: int = 20260907,
    population_size: int = DEFAULT_POPULATION_SIZE,
    generations: int = DEFAULT_GENERATIONS,
    minimum_trades_per_fold: int = DEFAULT_MIN_TRADES_PER_FOLD,
) -> dict[str, Any]:
    """Evoluciona reglas en validación temporal y evalúa una vez el holdout.

    ``predictions_by_algorithm`` debe contener probabilidades obtenidas sin
    entrenar con el año de cada fila. La función no muta las observaciones.
    """
    if population_size < 4 or generations < 1:
        raise ValueError("population_size must be >= 4 and generations must be >= 1")

    candidates = []
    for algorithm, cases in sorted(predictions_by_algorithm.items()):
        available_years = sorted({int(case["date"][:4]) for case in cases})
        if len(available_years) < 2:
            continue
        holdout_year = available_years[-1]
        validation_years = tuple(available_years[-3:-1])
        rng = np.random.default_rng(_seed_for_algorithm(algorithm, seed))
        population = [_random_genome(rng) for _ in range(population_size)]
        best: tuple[float, StrategyGenome, list[dict[str, Any]]] | None = None
        for _ in range(generations):
            scored = [
                (_fitness(folds := _folds(cases, genome, validation_years), minimum_trades_per_fold), genome, folds)
                for genome in population
            ]
            scored.sort(
                key=lambda item: (item[0], tuple(item[1].as_dict().values())),
                reverse=True,
            )
            if best is None or scored[0][0] > best[0]:
                best = scored[0]
            elites = [item[1] for item in scored[: max(2, population_size // 5)]]
            population = elites.copy()
            while len(population) < population_size:
                left, right = rng.choice(elites, size=2, replace=True)
                population.append(_breed(left, right, rng))
        assert best is not None
        fitness, genome, validation = best
        holdout = _folds(cases, genome, (holdout_year,))[0]
        validation_ok = all(
            fold["n_trades"] >= minimum_trades_per_fold and fold["roi_pct"] > 0
            for fold in validation
        )
        holdout_ok = (
            holdout["n_trades"] >= minimum_trades_per_fold and holdout["roi_pct"] > 0
        )
        candidates.append({
            "algorithm": algorithm,
            "genome": genome.as_dict(),
            "fitness": round(float(fitness), 6) if np.isfinite(fitness) else None,
            "validation_folds": validation,
            "holdout_fold": holdout,
            "validation_years": list(validation_years),
            "holdout_year": holdout_year,
            # Un solo año puede servir para observar un challenger, pero no
            # para aprobarlo: necesita dos validaciones anuales separadas.
            "eligible_for_review": bool(len(validation_years) >= 2 and validation_ok and holdout_ok),
            "reason": (
                None if len(validation_years) >= 2
                else "only one annual validation fold; observation only"
            ),
        })

    if not candidates:
        return {
            "eligible_for_review": False,
            "reason": "insufficient temporal folds for evolutionary evaluation",
            "candidates": [],
            "evolution": {
                "validation_years": [], "holdout_year": None,
                "population_size": population_size, "generations": generations,
                "minimum_trades_per_fold": minimum_trades_per_fold, "seed": seed,
            },
        }
    candidates.sort(
        key=lambda item: (
            item["fitness"] is not None,
            item["fitness"] if item["fitness"] is not None else float("-inf"),
        ),
        reverse=True,
    )
    winner = candidates[0]
    return {
        **winner,
        "candidates": candidates,
        "evolution": {
            "validation_years": winner["validation_years"], "holdout_year": winner["holdout_year"],
            "population_size": population_size, "generations": generations,
            "minimum_trades_per_fold": minimum_trades_per_fold, "seed": seed,
        },
    }
