"""LimitArb live en modo shadow, alimentado sólo por snapshots persistidos."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any

from outer_apis.kalshi.fees import order_fee
from database.historical.tennis.models import (
    LiveLimitArbDecision, LivePointSnapshot, LiveStatsSnapshot, Match,
)

STRATEGY_VERSION = "live_limit_arb_v1"
MIN_LIVE_EDGE = 0.08
MIN_GAMES_OBSERVED = 3
MIN_LOCKED_RETURN = 0.015


def _clamp(value: float, low: float = 0.02, high: float = 0.98) -> float:
    return max(low, min(high, value))


def _games_by_side(snapshot: LivePointSnapshot) -> tuple[int, int]:
    try:
        raw = json.loads(snapshot.raw_point_json or "{}")
    except (TypeError, ValueError):
        return (0, 0)
    a_games = b_games = 0
    for section in raw.values():
        if not isinstance(section, dict):
            continue
        for game in section.get("games") or []:
            side = game.get("winner_side")
            if side not in (1, 2):
                continue
            winner_is_a = (side == 1) == snapshot.player_a_is_flashscore_p1
            a_games += int(winner_is_a)
            b_games += int(not winner_is_a)
    return a_games, b_games


def _share(a: int | None, b: int | None) -> float | None:
    total = (a or 0) + (b or 0)
    return (a or 0) / total if total else None


def live_fair_probability(
    prior_a: float, point: LivePointSnapshot, stats: LiveStatsSnapshot,
) -> tuple[float, str, float, dict[str, Any]]:
    """Actualiza el prior en log-odds con evidencia live deliberadamente acotada."""
    games_a, games_b = _games_by_side(point)
    game_share = _share(games_a, games_b) or 0.5
    point_share = _share(stats.total_points_won_a, stats.total_points_won_b)
    point_share = 0.5 if point_share is None else point_share
    serve_a = (stats.aces_a or 0) - (stats.double_faults_a or 0)
    serve_b = (stats.aces_b or 0) - (stats.double_faults_b or 0)
    serve_delta = max(-1.0, min(1.0, (serve_a - serve_b) / 10.0))

    previous = (LiveStatsSnapshot.select()
                .where((LiveStatsSnapshot.match == stats.match)
                       & (LiveStatsSnapshot.observed_at < stats.observed_at))
                .order_by(LiveStatsSnapshot.observed_at.desc()).first())
    momentum = 0.0
    if previous is not None:
        prior_share = _share(previous.total_points_won_a, previous.total_points_won_b)
        if prior_share is not None:
            momentum = max(-0.15, min(0.15, point_share - prior_share))

    # Cap total: la evidencia parcial no puede mover el prior más de 0.9 logits.
    performance_score = (
        0.55 * (game_share - 0.5) * 2
        + 0.75 * (point_share - 0.5) * 2
        + 0.15 * serve_delta
        + 1.5 * momentum
    )
    adjustment = max(-0.9, min(0.9, performance_score))
    prior_a = _clamp(prior_a)
    fair_a = 1.0 / (1.0 + math.exp(-(math.log(prior_a / (1 - prior_a)) + adjustment)))
    state = "improving" if performance_score >= 0.12 else \
        "declining" if performance_score <= -0.12 else "stable"
    features = {
        "prior_a": prior_a, "games_a": games_a, "games_b": games_b,
        "game_share_a": game_share, "point_share_a": point_share,
        "serve_delta_a": serve_delta, "momentum_a": momentum,
        "logit_adjustment": adjustment,
    }
    return _clamp(fair_a), state, performance_score, features


def comeback_profile(match: Match, favourite_side: str) -> tuple[float | None, int]:
    """Perfil derivado de decisiones anteriores ya resueltas; no materializa otra tabla.

    2026-08-17, fix real encontrado auditando el trabajo de Codex antes
    de terminarlo. Dos problemas, no uno:
    1. La versión original solo comparaba contra `Match.player_a_name`
       -- si el mismo jugador real fue `player_b` en un partido anterior
       (Kalshi no fija de qué lado cae cada jugador), esas filas se
       perdían en silencio, subcontando la muestra sin ningún error
       visible. Es la misma clase de bug de matching por texto libre ya
       root-caused para `MatchHistory` (entidad canónica `Player`).
    2. Comparar solo por PRESENCIA del nombre en el partido no alcanza:
       hay que confirmar que fue el FAVORITO en esa decisión previa
       (`row.favourite_side`), no solo que jugó ese partido -- si
       apareció como el UNDERDOG de otro partido, esa fila no debe
       contar para el perfil de "cómo le va cuando el favorito está
       barato".
    El filtro por nombre en SQL (ambos lados) acota la consulta; la
    confirmación de lado favorito se hace en Python para no depender de
    una expresión condicional frágil en el WHERE."""
    favourite_name = match.player_a_name if favourite_side == "a" else match.player_b_name
    candidates = (
        LiveLimitArbDecision
        .select(LiveLimitArbDecision, Match)
        .join(Match)
        .where(
            LiveLimitArbDecision.status.in_(("won", "lost"))
            & (LiveLimitArbDecision.case == "favourite_undervalued")
            & ((Match.player_a_name == favourite_name) | (Match.player_b_name == favourite_name))
        )
    )
    rows = [
        row for row in candidates
        if (row.match.player_a_name if row.favourite_side == "a" else row.match.player_b_name)
        == favourite_name
    ]
    return ((sum(row.status == "won" for row in rows) / len(rows), len(rows))
            if rows else (None, 0))


def evaluate_live_limit_arb(
    match: Match, point: LivePointSnapshot | None, stats: LiveStatsSnapshot | None,
) -> LiveLimitArbDecision | None:
    """Crea una decisión shadow sólo con datos suficientes y el mismo tick."""
    if point is None or stats is None or point.probability_tick_id != stats.probability_tick_id:
        return None
    if point.games_observed < MIN_GAMES_OBSERVED:
        return None
    tick = point.probability_tick
    if None in (tick.market_price_a, tick.market_price_b, tick.model_fair_a, tick.model_fair_b):
        return None
    existing = LiveLimitArbDecision.get_or_none(
        (LiveLimitArbDecision.probability_tick == tick)
        & (LiveLimitArbDecision.strategy_version == STRATEGY_VERSION)
    )
    if existing is not None:
        return existing

    fair_a, state_a, score_a, features = live_fair_probability(
        float(tick.model_fair_a), point, stats,
    )
    favourite_side = "a" if tick.model_fair_a >= tick.model_fair_b else "b"
    fair = fair_a if favourite_side == "a" else 1.0 - fair_a
    market = float(tick.market_price_a if favourite_side == "a" else tick.market_price_b)
    dog_market = float(tick.market_price_b if favourite_side == "a" else tick.market_price_a)
    state = state_a if favourite_side == "a" else {
        "improving": "declining", "declining": "improving", "stable": "stable",
    }[state_a]
    score = score_a if favourite_side == "a" else -score_a
    signed_edge = fair - market
    if abs(signed_edge) < MIN_LIVE_EDGE:
        return None

    case = "favourite_undervalued" if signed_edge > 0 else "favourite_overvalued"
    # Si parece barata pero además está deteriorándose, no se compra el dip.
    if case == "favourite_undervalued" and state == "declining":
        return None
    adjustment = max(0.02, min(0.15, abs(signed_edge) * 0.5))
    if case == "favourite_undervalued":
        market_side, market_price = favourite_side, market
        limit_side, limit_base = ("b" if favourite_side == "a" else "a"), dog_market
    else:
        market_side, market_price = ("b" if favourite_side == "a" else "a"), dog_market
        limit_side, limit_base = favourite_side, market
    limit_price = _clamp(limit_base - adjustment, 0.01, 0.99)
    fees = (order_fee(market_price, category="tennis") or 0) \
        + (order_fee(limit_price, category="tennis") or 0)
    locked_return = max(0.0, 1.0 - market_price - limit_price - fees)
    if locked_return < MIN_LOCKED_RETURN:
        return None

    comeback_rate, comeback_n = comeback_profile(match, favourite_side)
    features.update({
        "fair_a": fair_a, "favourite_side": favourite_side,
        "signed_favourite_edge": signed_edge, "fees": fees,
    })
    return LiveLimitArbDecision.create(
        match=match, match_history=match.match_history_id, probability_tick=tick,
        point_snapshot=point, stats_snapshot=stats,
        strategy_version=STRATEGY_VERSION, decision_at=datetime.now(timezone.utc),
        favourite_side=favourite_side, case=case, market_probability=market,
        live_fair_probability=fair, edge=abs(signed_edge),
        performance_state=state, performance_score=score,
        comeback_rate=comeback_rate, comeback_sample_count=comeback_n,
        market_leg_side=market_side, market_leg_price=market_price,
        limit_leg_side=limit_side, limit_leg_price=limit_price,
        expected_locked_return=locked_return, features_json=json.dumps(features),
    )


def settle_live_limit_arb_decisions() -> int:
    """Resuelve propuestas contra el ganador real -- no simula fills todavía.

    IMPORTANTE, aclarado 2026-08-17 al terminar el trabajo de Codex:
    `status` ("won"/"lost") mide si comprar `market_leg_side` (la pata
    que se ejecutaría YA, a mercado) hubiera acertado -- NO si el arb
    completo (las 2 patas) se llegó a cerrar rentable. `expected_locked_
    return` es el resultado SOLO SI la pata límite (`limit_leg_side`)
    también llega a llenarse, cosa que hoy no se verifica contra
    profundidad real del CLOB (a diferencia del `fill_simulator` del
    LimitArb pre-match). Tratar `status`/`comeback_rate` como una señal
    de riesgo direccional del peor caso (solo la pata de mercado llena),
    no como el resultado económico real de la estrategia."""
    settled = 0
    for row in LiveLimitArbDecision.select().where(LiveLimitArbDecision.status == "open"):
        if row.match.winner not in ("a", "b"):
            continue
        row.status = "won" if row.match.winner == row.favourite_side else "lost"
        row.settled_at = datetime.now(timezone.utc)
        row.save(only=[LiveLimitArbDecision.status, LiveLimitArbDecision.settled_at])
        settled += 1
    return settled
