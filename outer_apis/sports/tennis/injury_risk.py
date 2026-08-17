"""Señal de riesgo de lesión, vía retiros/no-presentaciones reales
recientes -- pedido explícito del usuario 2026-07-31, caso real que lo
motivó: Svitolina jugando con una lesión de cadera real (walkover
confirmado el 2026-06-21 en Bad Homburg, retirada de otro torneo por lo
mismo a fines de junio).

Fuente: retiros canónicos de `MatchHistory`, derivados por consulta.

NO es un blend probabilístico como los demás (no hay forma honesta de
calibrar "cuántos puntos porcentuales vale un walkover de hace 3
semanas" sin datos reales de cuántos partidos post-lesión se ganan/
pierden) -- es un FLAG binario con un descuento fijo, chico y
documentado como heurística, no como algo calibrado."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from outer_apis.sports.tennis.recent_results_cache import fetch_and_cache as _fetch_and_cache

INJURY_LOOKBACK_DAYS = 45  # ventana en la que un retiro/walkover real se considera "riesgo vigente"
POST_INJURY_RECOVERY_DAYS = 90
POST_INJURY_PERFORMANCE_DAYS = 180
MAX_INJURY_PROBABILITY_PENALTY = 0.10


def calculate_post_injury_penalty(
    *,
    days_since_recovery: int,
    injury_active: bool = False,
    elo_delta: float | None = None,
    service_delta: float | None = None,
    return_delta: float | None = None,
) -> dict[str, float]:
    """Heurística acotada y auditable para deterioro posterior a lesión.

    Los deltas son ``reciente - promedio previo de 52 semanas``. Sólo un
    deterioro agrega castigo; una mejora nunca genera un bonus artificial.
    """
    recency = 0.05 if injury_active else 0.03 * max(
        0.0, 1.0 - max(0, days_since_recovery) / POST_INJURY_RECOVERY_DAYS,
    )
    impact_weight = 1.0 if injury_active else max(
        0.0,
        min(1.0, (POST_INJURY_PERFORMANCE_DAYS - max(0, days_since_recovery))
            / (POST_INJURY_PERFORMANCE_DAYS - POST_INJURY_RECOVERY_DAYS)),
    )
    elo = min(0.03, max(0.0, -(elo_delta or 0.0)) / 100.0 * 0.03) * impact_weight
    service = min(0.02, max(0.0, -(service_delta or 0.0)) / 10.0 * 0.02) * impact_weight
    returns = min(0.02, max(0.0, -(return_delta or 0.0)) / 0.10 * 0.02) * impact_weight
    total = min(MAX_INJURY_PROBABILITY_PENALTY, recency + elo + service + returns)
    return {
        "total": round(total, 6), "recency": round(recency, 6),
        "elo_decline": round(elo, 6), "service_decline": round(service, 6),
        "return_decline": round(returns, 6),
    }


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def get_post_injury_performance_penalty(
    player_name: str,
    *,
    as_of: date | str | None = None,
    surface: str | None = None,
) -> dict[str, Any] | None:
    """Castigo point-in-time usando retiro, Elo y stats históricas reales.

    La comparación usa partidos posteriores a la lesión (máximo 10) contra
    los 365 días anteriores. Ningún partido en o después de ``as_of`` entra
    al cálculo, requisito indispensable para backtests sin leakage.
    """
    from database.db import init_db_historical
    from database.historical.tennis.models import MatchHistory, MatchStats, Player

    evaluation_date = date.fromisoformat(str(as_of)) if as_of else date.today()
    init_db_historical()
    player = Player.select().where(Player.canonical_name ** player_name.strip()).first()
    if player is None:
        return None
    player_condition = (
        (MatchHistory.player_a == player.id) | (MatchHistory.player_b == player.id)
    )
    injury = (MatchHistory.select().where(
        player_condition
        & (MatchHistory.player_retired == player.id)
        & MatchHistory.retired
        & (MatchHistory.match_date < evaluation_date.isoformat())
    ).order_by(MatchHistory.match_date.desc(), MatchHistory.id.desc()).first())
    if injury is None:
        return {"flagged": False, "penalty": 0.0, "injury_date": None}

    injury_date = date.fromisoformat(injury.match_date)
    recovery_date = injury_date
    injury_active = False
    if injury.injury_end_date:
        try:
            reported_end = date.fromisoformat(injury.injury_end_date)
            # Never consume a recovery date that was still in the future.
            if reported_end <= evaluation_date:
                recovery_date = max(recovery_date, reported_end)
        except ValueError:
            pass
    baseline_start = injury_date - timedelta(days=365)
    rows = list(MatchHistory.select().where(
        player_condition
        & (MatchHistory.match_date >= baseline_start.isoformat())
        & (MatchHistory.match_date < evaluation_date.isoformat())
        & MatchHistory.winner.is_null(False)
    ).order_by(MatchHistory.match_date, MatchHistory.id))
    stats_by_match = {
        row.match_history_id: row for row in MatchStats.select().where(
            MatchStats.match_history.in_([match.id for match in rows]) & MatchStats.is_usable
        )
    } if rows else {}

    baseline: list[tuple[float | None, float | None, float | None]] = []
    recent: list[tuple[float | None, float | None, float | None]] = []
    for match in rows:
        if surface and match.surface and match.surface.casefold() != surface.casefold():
            continue
        side = "a" if match.player_a_id == player.id else "b"
        elo = getattr(match, f"player_{side}_surface_elo")
        if elo is None:
            elo = getattr(match, f"player_{side}_weighted_elo")
        stats = stats_by_match.get(match.id)
        service = return_rate = None
        if stats is not None:
            first_in = getattr(stats, f"first_serve_pct_{side}")
            first_won = getattr(stats, f"first_serve_points_won_pct_{side}")
            second_won = getattr(stats, f"second_serve_points_won_pct_{side}")
            if None not in (first_in, first_won, second_won):
                service = float(first_in) / 100.0 * float(first_won) + (1 - float(first_in) / 100.0) * float(second_won)
            opponent = "b" if side == "a" else "a"
            return_games = getattr(stats, f"return_games_won_{side}")
            opponent_service_games = getattr(stats, f"serve_games_played_{opponent}")
            if return_games is not None and opponent_service_games:
                return_rate = float(return_games) / float(opponent_service_games)
        values = (float(elo) if elo is not None else None, service, return_rate)
        (baseline if match.match_date < injury.match_date else recent).append(values)
    recent = recent[-10:]

    def delta(index: int) -> float | None:
        before = _mean([row[index] for row in baseline if row[index] is not None])
        after = _mean([row[index] for row in recent if row[index] is not None])
        return after - before if before is not None and after is not None else None

    elo_delta, service_delta, return_delta = delta(0), delta(1), delta(2)
    components = calculate_post_injury_penalty(
        days_since_recovery=(evaluation_date - recovery_date).days,
        injury_active=injury_active,
        elo_delta=elo_delta, service_delta=service_delta, return_delta=return_delta,
    )
    return {
        "flagged": components["total"] > 0,
        "penalty": components["total"],
        "components": components,
        "injury_date": injury.match_date,
        "recovery_date": recovery_date.isoformat(),
        "reason": injury.retirement_reason,
        "days_since_recovery": max(0, (evaluation_date - recovery_date).days),
        "elo_delta_vs_preinjury_52w": elo_delta,
        "service_delta_vs_preinjury_52w": service_delta,
        "return_delta_vs_preinjury_52w": return_delta,
        "baseline_matches": len(baseline), "post_injury_matches": len(recent),
    }


def get_injury_risk_flag(player_name: str, lookback_days: int = INJURY_LOOKBACK_DAYS) -> dict[str, Any] | None:
    """True/False de si `player_name` tuvo un retiro/no-presentación REAL
    (no del rival) dentro de los últimos `lookback_days`.

    Returns:
        {"flagged": bool, "date": str | None, "opponent": str | None,
         "tournament": str | None} -- el partido más reciente marcado, si
        hay más de uno.
        Fail-closed: None si no hay datos reales del jugador (nunca
        asume "sin riesgo" por ausencia de información, distinto de
        {"flagged": False, ...} que sí es una respuesta real de "no hay
        evidencia de lesión reciente")."""
    rows = _fetch_and_cache(player_name)
    if not rows:
        return None

    cutoff = date.today().toordinal() - lookback_days
    flagged_rows = [
        r for r in rows
        if r.get("retired_or_walkover") and r.get("date")
        and date.fromisoformat(r["date"]).toordinal() >= cutoff
    ]

    if not flagged_rows:
        return {"flagged": False, "date": None, "opponent": None, "tournament": None}

    most_recent = max(flagged_rows, key=lambda r: r["date"])
    return {
        "flagged": True, "date": most_recent["date"],
        "opponent": most_recent.get("opponent"), "tournament": most_recent.get("tournament"),
    }
