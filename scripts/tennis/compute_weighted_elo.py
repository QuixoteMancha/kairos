"""Recalcula Weighted Elo general/superficie y calibración point-in-time."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from math import isclose
import re

from database.bulk_update import bulk_update_by_id
from database.db import db_historical, init_db_historical
from database.historical.tennis import (
    MatchHistory, TennisEloCalibration, TennisPlayerStrength,
)
from models.volatility.sports.tennis.weighted_elo import (
    BASE_ELO, calibrate, combined_probability, fit_platt,
    regress_for_inactivity, update_pair,
)

_SURFACES = ("Hard", "Clay", "Grass")
_WEIGHTED_FIELDS = (
    MatchHistory.player_a_weighted_elo, MatchHistory.player_b_weighted_elo,
    MatchHistory.player_a_surface_elo, MatchHistory.player_b_surface_elo,
)


def _same_floats(stored, computed) -> bool:
    # Tolerancia relativa porque `FloatField` es REAL (float4) en PostgreSQL.
    return all(old is not None and isclose(old, new, rel_tol=1e-6) for old, new in zip(stored, computed))


def normalize_surface(raw: str | None) -> str | None:
    if not raw:
        return None
    value = raw.strip().lower()
    for surface in _SURFACES:
        if value.startswith(surface.lower()):
            return surface
    return None


def score_set_margin(score: str | None) -> int:
    pairs = [(int(a), int(b)) for a, b in re.findall(r"(?<!\d)(\d{1,2})\s*[-:]\s*(\d{1,2})(?!\d)", score or "")]
    if not pairs:
        return 1
    a_sets = sum(a > b for a, b in pairs)
    b_sets = sum(b > a for a, b in pairs)
    return max(1, abs(a_sets - b_sets))


def run_weighted_elo_backfill(tours: tuple[str, ...] = ("ATP", "WTA")) -> dict:
    init_db_historical()
    summary = {}
    now = datetime.now(timezone.utc)
    for tour in tours:
        overall: dict[int, float] = {}
        surface_rating: dict[tuple[int, str], float] = {}
        last_played: dict[int, date] = {}
        calibration_samples: dict[str, list[tuple[float, bool]]] = defaultdict(list)
        # Sólo las columnas que el cálculo usa, como tuplas (ver
        # compute_self_elo.py: instanciar el modelo completo por fila era
        # el costo dominante de la lectura).
        with db_historical.connection_context():
            rows = list(
                MatchHistory.select(
                    MatchHistory.id, MatchHistory.match_date, MatchHistory.surface,
                    MatchHistory.score, MatchHistory.player_a, MatchHistory.player_b,
                    MatchHistory.winner, *_WEIGHTED_FIELDS,
                ).where(
                    (MatchHistory.tour == tour)
                    & MatchHistory.player_a.is_null(False)
                    & MatchHistory.player_b.is_null(False)
                    & MatchHistory.winner.is_null(False)
                    & ~(MatchHistory.match_id.startswith("recentform:"))
                ).order_by(MatchHistory.match_date, MatchHistory.id).tuples()
            )
        processed = skipped = 0
        changes: list[tuple] = []
        for row_id, raw_date, raw_surface, score, a_id, b_id, winner_id, *stored in rows:
            try:
                played = date.fromisoformat(raw_date)
            except (TypeError, ValueError):
                skipped += 1
                continue
            surface = normalize_surface(raw_surface)
            if surface is None:
                skipped += 1
                continue
            for player_id in (a_id, b_id):
                current = overall.get(player_id, BASE_ELO)
                previous = last_played.get(player_id)
                if previous:
                    current = regress_for_inactivity(current, (played - previous).days)
                overall[player_id] = current
                surface_rating.setdefault((player_id, surface), BASE_ELO)
            oa, ob = overall[a_id], overall[b_id]
            sa, sb = surface_rating[(a_id, surface)], surface_rating[(b_id, surface)]
            raw_probability = combined_probability(oa, ob, sa, sb)
            a_won = winner_id == a_id
            computed = (oa, ob, sa, sb)
            if not _same_floats(stored, computed):
                changes.append((row_id, *computed))
            # MatchHistory no asigna A/B aleatoriamente: player_a suele
            # ser el dueño de la página backfilleada. Añadir ambos lados
            # evita que Platt aprenda ese sesgo de orientación.
            calibration_samples[surface].append((raw_probability, a_won))
            calibration_samples[surface].append((1.0 - raw_probability, not a_won))
            margin = score_set_margin(score)
            overall[a_id], overall[b_id] = update_pair(oa, ob, a_won, margin)
            surface_rating[(a_id, surface)], surface_rating[(b_id, surface)] = update_pair(sa, sb, a_won, margin)
            last_played[a_id] = last_played[b_id] = played
            processed += 1

        calibrations = {surface: fit_platt(samples) for surface, samples in calibration_samples.items()}
        sample_counts = {surface: len(samples) for surface, samples in calibration_samples.items()}
        del calibration_samples  # ~2 tuplas por partido; no hace falta retenerlas durante las escrituras

        with db_historical.connection_context(), db_historical.atomic():
            # Escritura sólo de filas cuyo Elo cambió, en lotes (ver
            # compute_self_elo.py).
            updated = bulk_update_by_id(db_historical, MatchHistory, _WEIGHTED_FIELDS, changes)
            TennisPlayerStrength.delete().where(TennisPlayerStrength.tour == tour).execute()
            strengths = [
                {
                    "player": player_id, "tour": tour, "overall_elo": rating,
                    "hard_elo": surface_rating.get((player_id, "Hard")),
                    "clay_elo": surface_rating.get((player_id, "Clay")),
                    "grass_elo": surface_rating.get((player_id, "Grass")),
                    "last_match_date": last_played.get(player_id), "updated_at": now,
                }
                for player_id, rating in overall.items()
            ]
            for start in range(0, len(strengths), 1000):
                TennisPlayerStrength.insert_many(strengths[start:start + 1000]).execute()
            for surface, (intercept, slope) in calibrations.items():
                samples_n = sample_counts[surface]
                TennisEloCalibration.insert(
                    tour=tour, surface=surface, intercept=intercept, slope=slope,
                    sample_count=samples_n, fitted_through=max(last_played.values(), default=None),
                    updated_at=now,
                ).on_conflict(
                    conflict_target=(TennisEloCalibration.tour, TennisEloCalibration.surface),
                    update={
                        TennisEloCalibration.intercept: intercept,
                        TennisEloCalibration.slope: slope,
                        TennisEloCalibration.sample_count: samples_n,
                        TennisEloCalibration.fitted_through: max(last_played.values(), default=None),
                        TennisEloCalibration.updated_at: now,
                    },
                ).execute()
        summary[tour] = {"processed": processed, "skipped": skipped, "updated": updated, "players": len(overall)}
    return summary


if __name__ == "__main__":
    print(run_weighted_elo_backfill())
