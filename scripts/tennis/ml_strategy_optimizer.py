"""Laboratorio ML offline para `BetOnFavourite`.

Construye features point-in-time, compara challengers con folds anuales y
persiste artefactos/reportes. Deliberadamente no importa estrategias ni APIs
de ejecución: este módulo no puede colocar apuestas ni cambiar feature flags.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.base import clone

from database.db import db_historical, init_db_historical
from database.historical.tennis.models import MatchHistory, MatchStats
from database.historical.workerstate import TennisMlExperiment
from scripts.tennis.bet_on_favourite_backtest import _odds_for_match, simulate
from scripts.tennis.evolutionary_strategy_optimizer import evolve_strategy_rules
from outer_apis.sports.tennis.age_fatigue_veto import age_fatigue_veto
from outer_apis.sports.tennis.h2h_veto import MIN_H2H, has_sufficient_h2h, h2h_veto
from outer_apis.sports.tennis.injury_risk import calculate_post_injury_penalty
from strategies.bet_on_favourite import _relative_injury_adjustment


MIN_TRAIN_ROWS = 500
MIN_REVIEW_TRADES_PER_FOLD = 25
EDGE_THRESHOLD = 0.03
MAX_ODDS = 3.0
VALIDATION_FOLD_COUNT = 2
FEATURE_COLUMNS = (
    "market_probability", "weighted_elo_diff", "surface_elo_diff",
    "recent_wr_diff", "elo_slope_5_diff", "elo_slope_10_diff",
    "service_effectiveness_diff", "return_games_rate_diff",
    "recent_point_share_diff", "recent_return_points_effectiveness_diff",
    "favorite_tournament_minutes", "underdog_tournament_minutes",
    "tournament_minutes_diff", "favorite_tournament_matches",
    "underdog_tournament_matches", "tournament_matches_diff",
    "tour", "surface", "tournament_stage",
)
CATEGORICAL_COLUMNS = ("tour", "surface", "tournament_stage")
NUMERIC_COLUMNS = tuple(column for column in FEATURE_COLUMNS if column not in CATEGORICAL_COLUMNS)


def _slope(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    x_mean = (len(values) - 1) / 2
    y_mean = sum(values) / len(values)
    denominator = sum((i - x_mean) ** 2 for i in range(len(values)))
    return sum((i - x_mean) * (value - y_mean) for i, value in enumerate(values)) / denominator


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _service_effectiveness(stats: MatchStats, side: str) -> float | None:
    first_in = getattr(stats, f"first_serve_pct_{side}")
    first_won = getattr(stats, f"first_serve_points_won_pct_{side}")
    second_won = getattr(stats, f"second_serve_points_won_pct_{side}")
    if None in (first_in, first_won, second_won):
        return None
    first_fraction = float(first_in) / 100.0
    return first_fraction * float(first_won) + (1.0 - first_fraction) * float(second_won)


def _stats_features(stats: MatchStats, side: str, opponent_side: str) -> tuple[float | None, float | None]:
    service = _service_effectiveness(stats, side)
    return_games = getattr(stats, f"return_games_won_{side}")
    opponent_service_games = getattr(stats, f"serve_games_played_{opponent_side}")
    return_rate = None
    if return_games is not None and opponent_service_games:
        return_rate = float(return_games) / float(opponent_service_games)
    return service, return_rate


def _point_share(stats: MatchStats, side: str, opponent_side: str) -> float | None:
    own = getattr(stats, f"total_points_won_{side}")
    opponent = getattr(stats, f"total_points_won_{opponent_side}")
    return float(own) / (float(own) + float(opponent)) if own is not None and opponent is not None and own + opponent else None


def _return_points_effectiveness(stats: MatchStats, side: str) -> float | None:
    first = getattr(stats, f"first_return_points_won_pct_{side}")
    second = getattr(stats, f"second_return_points_won_pct_{side}")
    return (float(first) + float(second)) / 2.0 if first is not None and second is not None else None


def _tournament_stage(round_name: str | None) -> str:
    value = (round_name or "").strip().lower()
    if value.startswith("qualification"):
        return "Qualifying"
    if "quarterfinal" in value:
        return "Quarterfinal"
    if "semifinal" in value:
        return "Semifinal"
    if value == "final":
        return "Final"
    if "round of 16" in value:
        return "RoundOf16"
    return "Early" if value else "Unknown"


def collect_dataset() -> list[dict[str, Any]]:
    """Una observación por partido, orientada al favorito de mercado."""
    init_db_historical()
    results: dict[int, deque[bool]] = defaultdict(lambda: deque(maxlen=10))
    elos: dict[int, deque[float]] = defaultdict(lambda: deque(maxlen=10))
    services: dict[int, deque[float]] = defaultdict(lambda: deque(maxlen=10))
    returns: dict[int, deque[float]] = defaultdict(lambda: deque(maxlen=10))
    point_shares: dict[int, deque[float]] = defaultdict(lambda: deque(maxlen=5))
    return_points: dict[int, deque[float]] = defaultdict(lambda: deque(maxlen=5))
    performance: dict[int, list[tuple[date, float | None, float | None, float | None]]] = defaultdict(list)
    injury_state: dict[int, dict[str, Any]] = {}
    # El laboratorio debe respetar las mismas condiciones fail-closed que
    # BetOnFavourite: sin edad/duración acumulada/H2H previo comprobable no
    # se genera un candidato que luego pudiera recomendarse por error.
    tournament_minutes: dict[tuple[int, int], list[float | None]] = defaultdict(list)
    h2h_wins: dict[tuple[int, int], dict[int, int]] = defaultdict(dict)
    rows: list[dict[str, Any]] = []
    with db_historical.connection_context():
        stats_by_match = {
            row.match_history_id: row
            for row in MatchStats.select().where(
                MatchStats.match_history.is_null(False)
                & MatchStats.is_usable
                & MatchStats.player_a_is_flashscore_p1.is_null(False)
            )
        }
        matches = list(
            MatchHistory.select().where(
                MatchHistory.winner.is_null(False)
                & MatchHistory.player_a.is_null(False)
                & MatchHistory.player_b.is_null(False)
                & ~(MatchHistory.match_id.startswith("recentform:"))
            ).order_by(MatchHistory.match_date, MatchHistory.id)
        )
        for match in matches:
            try:
                played = date.fromisoformat(match.match_date)
            except (TypeError, ValueError):
                continue
            odds = _odds_for_match(match)

            def injury_penalty(player_id: int) -> float:
                state = injury_state.get(player_id)
                if state is None:
                    return 0.0
                recovery = state["date"]
                end = state.get("end")
                active = False
                if end and end <= played:
                    recovery = max(recovery, end)
                post = [item for item in performance[player_id] if item[0] >= state["date"]][-10:]

                def delta(index: int) -> float | None:
                    before = [item[index] for item in state["baseline"] if item[index] is not None]
                    after = [item[index] for item in post if item[index] is not None]
                    return sum(after) / len(after) - sum(before) / len(before) if before and after else None

                return calculate_post_injury_penalty(
                    days_since_recovery=max(0, (played - recovery).days), injury_active=active,
                    elo_delta=delta(1), service_delta=delta(2), return_delta=delta(3),
                )["total"]
            elo_values = (
                match.player_a_weighted_elo, match.player_b_weighted_elo,
                match.player_a_surface_elo, match.player_b_surface_elo,
            )
            if odds and min(odds) > 1 and odds[0] != odds[1] and None not in elo_values:
                a_favorite = odds[0] < odds[1]
                fav_id, dog_id = (
                    (match.player_a_id, match.player_b_id) if a_favorite
                    else (match.player_b_id, match.player_a_id)
                )
                fav_odds, dog_odds = (odds if a_favorite else odds[::-1])
                fav_overall, dog_overall = (
                    elo_values[:2] if a_favorite else elo_values[1::-1]
                )
                fav_surface, dog_surface = (
                    elo_values[2:] if a_favorite else elo_values[:1:-1]
                )
                fav_player = match.player_a if a_favorite else match.player_b
                prior_durations = tournament_minutes.get((fav_id, match.tournament_id), [])
                fatigue_data_complete = bool(
                    fav_player.birthdate is not None
                    and match.tournament_id is not None
                    and all(duration is not None for duration in prior_durations)
                )
                fav_age = (
                    (played - fav_player.birthdate).days / 365.25
                    if fav_player.birthdate is not None else None
                )
                fav_cumulative_minutes = sum(prior_durations) if fatigue_data_complete else None
                dog_prior_durations = tournament_minutes.get((dog_id, match.tournament_id), [])
                dog_cumulative_minutes = sum(dog_prior_durations) if all(value is not None for value in dog_prior_durations) else None
                pair_key = (min(fav_id, dog_id), max(fav_id, dog_id))
                prior_h2h = h2h_wins.get(pair_key, {})
                n_prior_h2h = sum(prior_h2h.values())
                fav_h2h_wr = (
                    prior_h2h.get(fav_id, 0) / n_prior_h2h
                    if n_prior_h2h >= MIN_H2H else None
                )
                implied_fav, implied_dog = 1 / fav_odds, 1 / dog_odds
                fair_market = implied_fav / (implied_fav + implied_dog)
                fav_results, dog_results = list(results[fav_id]), list(results[dog_id])
                fav_elos, dog_elos = list(elos[fav_id]), list(elos[dog_id])
                evidence_is_complete = (
                    fatigue_data_complete
                    and has_sufficient_h2h(n_prior_h2h)
                    and not age_fatigue_veto(fav_age, fav_cumulative_minutes)
                    and not h2h_veto(fav_h2h_wr)
                )
                if evidence_is_complete:
                    rows.append({
                        "match_id": match.match_id,
                        "date": played.isoformat(),
                        "tour": match.tour or "Unknown",
                        "surface": (match.surface or "Unknown").title(),
                        "favorite_decimal_odds": float(fav_odds),
                        "market_probability": fair_market,
                        "weighted_elo_diff": float(fav_overall) - float(dog_overall),
                        "surface_elo_diff": float(fav_surface) - float(dog_surface),
                        "recent_wr_diff": (
                            _mean([float(x) for x in fav_results]) - _mean([float(x) for x in dog_results])
                            if fav_results and dog_results else None
                        ),
                        "elo_slope_5_diff": (
                            _slope(fav_elos[-5:]) - _slope(dog_elos[-5:])
                            if len(fav_elos) >= 5 and len(dog_elos) >= 5 else None
                        ),
                        "elo_slope_10_diff": (
                            _slope(fav_elos) - _slope(dog_elos)
                            if len(fav_elos) >= 10 and len(dog_elos) >= 10 else None
                        ),
                        "service_effectiveness_diff": (
                            _mean(list(services[fav_id])) - _mean(list(services[dog_id]))
                            if services[fav_id] and services[dog_id] else None
                        ),
                    "return_games_rate_diff": (
                            _mean(list(returns[fav_id])) - _mean(list(returns[dog_id]))
                            if returns[fav_id] and returns[dog_id] else None
                    ),
                    "recent_point_share_diff": (
                        _mean(list(point_shares[fav_id])) - _mean(list(point_shares[dog_id]))
                        if point_shares[fav_id] and point_shares[dog_id] else None
                    ),
                    "recent_return_points_effectiveness_diff": (
                        _mean(list(return_points[fav_id])) - _mean(list(return_points[dog_id]))
                        if return_points[fav_id] and return_points[dog_id] else None
                    ),
                    "favorite_tournament_minutes": fav_cumulative_minutes,
                    "underdog_tournament_minutes": dog_cumulative_minutes,
                    "tournament_minutes_diff": (
                        fav_cumulative_minutes - dog_cumulative_minutes
                        if fav_cumulative_minutes is not None and dog_cumulative_minutes is not None else None
                    ),
                    "favorite_tournament_matches": len(prior_durations),
                    "underdog_tournament_matches": len(dog_prior_durations),
                    "tournament_matches_diff": len(prior_durations) - len(dog_prior_durations),
                    "tournament_stage": _tournament_stage(match.round),
                    "statistics_evidence_complete": bool(
                        point_shares[fav_id] and point_shares[dog_id]
                        and return_points[fav_id] and return_points[dog_id]
                    ),
                        "favorite_injury_penalty": injury_penalty(fav_id),
                        "underdog_injury_penalty": injury_penalty(dog_id),
                        "hit": match.winner_id == fav_id,
                    })

            a_won = match.winner_id == match.player_a_id
            results[match.player_a_id].append(a_won)
            results[match.player_b_id].append(not a_won)
            if match.player_a_weighted_elo is not None:
                elos[match.player_a_id].append(float(match.player_a_weighted_elo))
            if match.player_b_weighted_elo is not None:
                elos[match.player_b_id].append(float(match.player_b_weighted_elo))
            stats = stats_by_match.get(match.id)
            for player_id in (match.player_a_id, match.player_b_id):
                if match.retired and match.player_retired_id == player_id:
                    cutoff = played.toordinal() - 365
                    end = None
                    if match.injury_end_date:
                        try:
                            end = date.fromisoformat(match.injury_end_date)
                        except ValueError:
                            pass
                    injury_state[player_id] = {
                        "date": played, "end": end,
                        "baseline": [item for item in performance[player_id] if item[0].toordinal() >= cutoff],
                    }
            if match.tournament_id is not None:
                match_minutes = match.duration.total_minutes if match.duration_id else None
                tournament_minutes[(match.player_a_id, match.tournament_id)].append(match_minutes)
                tournament_minutes[(match.player_b_id, match.tournament_id)].append(match_minutes)
            if match.winner_id in (match.player_a_id, match.player_b_id):
                pair_key = (min(match.player_a_id, match.player_b_id), max(match.player_a_id, match.player_b_id))
                bucket = h2h_wins[pair_key]
                bucket[match.winner_id] = bucket.get(match.winner_id, 0) + 1
            if stats is not None:
                for player_id, side, opponent_side in (
                    (match.player_a_id, "a", "b"), (match.player_b_id, "b", "a"),
                ):
                    service, return_rate = _stats_features(stats, side, opponent_side)
                    point_share = _point_share(stats, side, opponent_side)
                    return_point = _return_points_effectiveness(stats, side)
                    if service is not None:
                        services[player_id].append(service)
                    if return_rate is not None:
                        returns[player_id].append(return_rate)
                    if point_share is not None:
                        point_shares[player_id].append(point_share)
                    if return_point is not None:
                        return_points[player_id].append(return_point)
                    elo = (
                        match.player_a_surface_elo if side == "a" else match.player_b_surface_elo
                    )
                    if elo is None:
                        elo = match.player_a_weighted_elo if side == "a" else match.player_b_weighted_elo
                    performance[player_id].append((
                        played, float(elo) if elo is not None else None, service, return_rate,
                    ))
            else:
                for player_id, elo in (
                    (match.player_a_id, match.player_a_surface_elo or match.player_a_weighted_elo),
                    (match.player_b_id, match.player_b_surface_elo or match.player_b_weighted_elo),
                ):
                    performance[player_id].append((played, float(elo) if elo is not None else None, None, None))
    return rows


def _preprocessor() -> ColumnTransformer:
    numeric = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
    ])
    categorical = Pipeline([
        ("imputer", SimpleImputer(strategy="most_frequent")),
        ("one_hot", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
    ])
    return ColumnTransformer([
        ("numeric", numeric, list(range(len(NUMERIC_COLUMNS)))),
        ("categorical", categorical, list(range(len(NUMERIC_COLUMNS), len(FEATURE_COLUMNS)))),
    ])


def model_candidates() -> dict[str, Any]:
    return {
        "logistic_regression": LogisticRegression(max_iter=1000, C=0.5),
        "random_forest": RandomForestClassifier(
            n_estimators=120, max_depth=6, min_samples_leaf=20,
            random_state=42, n_jobs=1,
        ),
        "hist_gradient_boosting": HistGradientBoostingClassifier(
            max_iter=120, max_depth=4, learning_rate=0.05,
            min_samples_leaf=20, random_state=42,
        ),
    }


def _matrix(rows: list[dict[str, Any]]) -> np.ndarray:
    return np.asarray([[row.get(column) for column in FEATURE_COLUMNS] for row in rows], dtype=object)


def dataset_sha256(rows: list[dict[str, Any]]) -> str:
    identity = "\n".join(json.dumps(
        {key: row.get(key) for key in (
            "match_id", "date", *FEATURE_COLUMNS,
            "favorite_injury_penalty", "underdog_injury_penalty",
            # A corrected winner changes the supervised training set even
            # when all pre-match features are unchanged.  Omitting it made
            # artifact directories and persisted experiment identities lie.
            "hit",
        )},
        sort_keys=True, default=str,
    ) for row in rows)
    return sha256(identity.encode()).hexdigest()


def stats_orientation_quality() -> dict[str, Any]:
    usable = MatchStats.select().where(MatchStats.is_usable == True).count()
    audited = MatchStats.select().where(
        (MatchStats.is_usable == True)
        & MatchStats.player_a_is_flashscore_p1.is_null(False)
    ).count()
    return {
        "usable_stats": usable,
        "orientation_audited": audited,
        "orientation_pending": max(0, usable - audited),
        "orientation_coverage_pct": round(100.0 * audited / usable, 2) if usable else 100.0,
    }


def _injury_adjusted_probabilities(rows: list[dict[str, Any]], probabilities: np.ndarray) -> np.ndarray:
    return np.asarray([
        _relative_injury_adjustment(
            float(probability), float(row.get("favorite_injury_penalty", 0.0)),
            float(row.get("underdog_injury_penalty", 0.0)),
        )
        for row, probability in zip(rows, probabilities)
    ])


def _evaluate_predictions(rows: list[dict[str, Any]], probabilities: np.ndarray) -> dict[str, Any]:
    probabilities = _injury_adjusted_probabilities(rows, probabilities)
    labels = np.asarray([int(row["hit"]) for row in rows])
    selected = []
    for row, probability in zip(rows, probabilities):
        edge = float(probability) - float(row["market_probability"])
        if edge >= EDGE_THRESHOLD and row["favorite_decimal_odds"] <= MAX_ODDS:
            selected.append({**row, "model_probability": float(probability), "model_edge": edge})
    eligible_market_favorites = [
        row for row in rows if row["favorite_decimal_odds"] <= MAX_ODDS
    ]
    odds_bands = {}
    for label, lower, upper in (
        ("1.00-1.30", 1.0, 1.3), ("1.30-1.60", 1.3, 1.6),
        ("1.60-2.00", 1.6, 2.0), ("2.00-3.00", 2.0, 3.000001),
    ):
        band = [row for row in selected if lower <= row["favorite_decimal_odds"] < upper]
        odds_bands[label] = simulate(band)
    return {
        "n": len(rows),
        "brier": round(float(brier_score_loss(labels, probabilities)), 6),
        "log_loss": round(float(log_loss(labels, probabilities, labels=[0, 1])), 6),
        "strategy": simulate(selected),
        "market_favorite_baseline": simulate(eligible_market_favorites),
        "strategy_by_odds_band": odds_bands,
    }


def train_and_evaluate(
    rows: list[dict[str, Any]], artifact_root: str | Path,
    *, dataset_quality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Entrena challengers secuencialmente y devuelve reportes reproducibles."""
    rows = sorted(rows, key=lambda row: (row["date"], row["match_id"]))
    dataset_hash = dataset_sha256(rows)
    artifact_dir = Path(artifact_root) / dataset_hash
    artifact_dir.mkdir(parents=True, exist_ok=True)
    reports = {}
    evolutionary_predictions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    available_years = sorted({int(row["date"][:4]) for row in rows})
    evolution_years = tuple(available_years[-3:])
    # Rolling calendar folds keep the evaluator meaningful after 2026.  The
    # old fixed (2025, 2026) tuple silently stopped assessing recent years,
    # while its persisted artifact remained trained only through 2025.
    evaluation_years = tuple(available_years[-VALIDATION_FOLD_COUNT:])
    model_years = tuple(sorted(set(evaluation_years) | set(evolution_years)))
    for name, estimator in model_candidates().items():
        folds = []
        for year in model_years:
            training = [row for row in rows if int(row["date"][:4]) < year]
            evaluation = [row for row in rows if int(row["date"][:4]) == year]
            if len(training) < MIN_TRAIN_ROWS or not evaluation:
                continue
            pipeline = Pipeline([("features", _preprocessor()), ("model", clone(estimator))])
            pipeline.fit(_matrix(training), np.asarray([int(row["hit"]) for row in training]))
            probabilities = pipeline.predict_proba(_matrix(evaluation))[:, 1]
            adjusted_probabilities = _injury_adjusted_probabilities(evaluation, probabilities)
            if year in evaluation_years:
                folds.append({"year": year, **_evaluate_predictions(evaluation, probabilities)})
            if year in evolution_years:
                evolutionary_predictions[name].extend(
                    {**row, "model_probability": float(probability)}
                    for row, probability in zip(evaluation, adjusted_probabilities)
                )
        if not folds:
            reports[name] = {"folds": [], "eligible_for_review": False, "reason": "insufficient data"}
            continue
        # Validation is strictly historical above. Once it is complete, a
        # research artifact may use every currently known row; otherwise the
        # saved model permanently lags one full evaluation year.
        final_pipeline = Pipeline([("features", _preprocessor()), ("model", clone(estimator))])
        final_pipeline.fit(_matrix(rows), np.asarray([int(row["hit"]) for row in rows]))
        artifact_path = artifact_dir / f"{name}.joblib"
        joblib.dump(final_pipeline, artifact_path)
        validation_complete = len(folds) == len(evaluation_years)
        eligible = validation_complete and all(
            fold["strategy"]["n_trades"] >= MIN_REVIEW_TRADES_PER_FOLD
            and fold["strategy"]["roi_pct"] > 0
            for fold in folds
        )
        reports[name] = {
            "folds": folds, "eligible_for_review": eligible,
            "artifact_path": str(artifact_path),
            "artifact_training_through": rows[-1]["date"],
            "validation_complete": validation_complete,
            # The return simulation uses bookmaker odds, not historical
            # executable Kalshi fills.  A positive fold can merit research,
            # never automatic promotion or an execution claim.
            "execution_ready": False,
            "review_scope": "research_proxy_pricing_only",
        }
    # Las reglas evolucionan sólo con predicciones ya out-of-sample; el
    # último año disponible es siempre el holdout que no toca el fitness.
    reports["evolutionary_rules"] = evolve_strategy_rules(evolutionary_predictions)
    return {
        "dataset_sha256": dataset_hash,
        "sample_count": len(rows),
        "training_through": rows[-1]["date"] if rows else None,
        "models": reports,
        "dataset_quality": dataset_quality or {"orientation_status": "not_measured"},
        "evaluation_assumptions": {
            "pricing": "tennisexplorer_bookmaker_decimal_odds_proxy",
            "payout": "raw_bookmaker_odds_including_embedded_vig",
            "kalshi_fees_applied": False,
            "reason": "historical Kalshi execution prices are unavailable for most training matches",
            "stake_fraction_per_trade": 0.01,
            "max_daily_exposure_fraction": 0.5,
            "post_injury_adjustment": "relative_probability_mass_penalty_capped_at_10pct",
        },
    }


def persist_report(report: dict[str, Any]) -> None:
    now = datetime.now(timezone.utc)
    with db_historical.atomic():
        for algorithm, result in report["models"].items():
            TennisMlExperiment.create(
                algorithm=algorithm,
                dataset_sha256=report["dataset_sha256"],
                sample_count=report["sample_count"],
                training_through=report["training_through"],
                metrics_json=json.dumps(result, default=str),
                parameters_json=json.dumps({
                    "features": FEATURE_COLUMNS, "edge": EDGE_THRESHOLD,
                    "max_odds": MAX_ODDS, "promotion": "manual_only",
                    "evolution": result.get("evolution"),
                    "genome": result.get("genome"),
                    "dataset_quality": report.get("dataset_quality", {}),
                }),
                artifact_path=result.get("artifact_path"),
                eligible_for_review=result.get("eligible_for_review", False),
                promotion_status=(
                    "research_proxy_pricing"
                    if report.get("dataset_quality", {}).get("orientation_pending") == 0
                    else "research_proxy_pricing_partial_orientation"
                ),
                created_at=now,
            )


def run_optimizer(artifact_root: str | Path | None = None) -> dict[str, Any]:
    root = artifact_root or os.getenv("TENNIS_ML_ARTIFACT_DIR", "/data/ml/tennis")
    rows = collect_dataset()
    report = train_and_evaluate(
        rows, root, dataset_quality=stats_orientation_quality(),
    )
    persist_report(report)
    return report


if __name__ == "__main__":
    print(json.dumps(run_optimizer(), indent=2, ensure_ascii=False))
