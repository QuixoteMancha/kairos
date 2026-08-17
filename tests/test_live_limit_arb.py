"""Tests de `LiveLimitArb` (shadow, NUNCA coloca órdenes reales) --
2026-08-17. Terminé el trabajo que dejó a medio camino la sesión de
Codex (se cortó por límite de uso): auditado, arreglado un bug real de
matching, wireado al worker live, expuesta la API, y testeado end-to-end
antes de deployar."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis.models import (
    LiveLimitArbDecision, LivePointSnapshot, LiveStatsSnapshot, Match,
    ProbabilityTick,
)
from strategies.live_limit_arb import (
    comeback_profile, evaluate_live_limit_arb, live_fair_probability,
    settle_live_limit_arb_decisions,
)


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    models = (LiveLimitArbDecision, LiveStatsSnapshot, LivePointSnapshot, ProbabilityTick, Match)
    with db_historical.connection_context():
        for m in models:
            m.delete().execute()
    yield
    with db_historical.connection_context():
        for m in models:
            m.delete().execute()


def _match(**overrides) -> Match:
    base = {
        "market_id": f"KX-{overrides.pop('suffix', '1')}", "ticker_a": "A", "ticker_b": "B",
        "tour": "ATP", "player_a_name": "Favourite Player", "player_b_name": "Underdog Player",
        "best_of": 3, "detected_at": datetime.now(timezone.utc),
        "flashscore_match_id": "fs1", "player_a_is_flashscore_p1": True,
    }
    base.update(overrides)
    return Match.create(**base)


def _tick(match: Match, *, market_a=0.62, fair_a=0.72, offset_s=0) -> ProbabilityTick:
    return ProbabilityTick.create(
        match=match,
        observed_at=datetime.now(timezone.utc) + timedelta(seconds=offset_s),
        market_price_a=market_a, market_price_b=round(1 - market_a, 4),
        model_fair_a=fair_a, model_fair_b=round(1 - fair_a, 4),
    )


def _points(*, a_games=4, b_games=1) -> dict:
    games = (
        [{"winner_side": 1, "points": ["0:15"], "break_points": []}] * a_games
        + [{"winner_side": 2, "points": ["0:15"], "break_points": []}] * b_games
    )
    return {"set_1": {"games": games}}


def _point_snapshot(match: Match, tick: ProbabilityTick, **overrides) -> LivePointSnapshot:
    points = overrides.pop("points", _points())
    base = {
        "match": match, "probability_tick": tick, "flashscore_match_id": "fs1",
        "player_a_is_flashscore_p1": True, "observed_at": datetime.now(timezone.utc),
        "games_observed": sum(len(s.get("games", [])) for s in points.values()),
        "raw_point_json": json.dumps(points),
    }
    base.update(overrides)
    return LivePointSnapshot.create(**base)


def _stats_snapshot(match: Match, tick: ProbabilityTick, **overrides) -> LiveStatsSnapshot:
    base = {
        "match": match, "probability_tick": tick, "flashscore_match_id": "fs1",
        "player_a_is_flashscore_p1": True, "observed_at": datetime.now(timezone.utc),
        "raw_stats_json": "{}",
        "aces_a": 5, "aces_b": 2, "double_faults_a": 1, "double_faults_b": 3,
        "total_points_won_a": 30, "total_points_won_b": 18,
    }
    base.update(overrides)
    return LiveStatsSnapshot.create(**base)


# --- live_fair_probability: dirección y acotamiento ---

def test_live_fair_probability_moves_toward_leading_side():
    match = _match()
    tick = _tick(match)
    point = _point_snapshot(match, tick)  # A domina games 4-1
    stats = _stats_snapshot(match, tick)  # A domina puntos y saque

    fair_a, state, score, features = live_fair_probability(0.72, point, stats)

    assert fair_a > 0.72, "si A está dominando, el fair ajustado debe subir, no bajar"
    assert state == "improving"
    assert score > 0
    assert 0.02 <= fair_a <= 0.98


def test_live_fair_probability_never_leaves_valid_range():
    match = _match()
    tick = _tick(match)
    # B domina absolutamente -- caso extremo, el ajuste está acotado (cap 0.9 logits).
    point = _point_snapshot(match, tick, points=_points(a_games=0, b_games=6))
    stats = _stats_snapshot(
        match, tick, aces_a=0, aces_b=10, double_faults_a=8, double_faults_b=0,
        total_points_won_a=5, total_points_won_b=40,
    )
    fair_a, state, score, _ = live_fair_probability(0.95, point, stats)
    assert 0.02 <= fair_a <= 0.98
    assert state == "declining"


def test_momentum_uses_only_strictly_past_snapshots_no_lookahead():
    """El snapshot 'previous' se busca con observed_at < el actual --
    nunca debe usar un snapshot futuro para calcular momentum. Índice
    único real (match, probability_tick) -> se necesitan 2 ticks."""
    match = _match()
    tick_old = _tick(match, offset_s=0)
    tick_new = _tick(match, offset_s=60)
    older = _stats_snapshot(
        match, tick_old, total_points_won_a=10, total_points_won_b=10,
        observed_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    point = _point_snapshot(match, tick_new)
    newer = _stats_snapshot(
        match, tick_new, total_points_won_a=30, total_points_won_b=10,
        observed_at=datetime.now(timezone.utc),
    )
    # Sin momentum (solo older existe) vs con momentum (newer ve a older).
    _, _, score_without_history, _ = live_fair_probability(0.5, point, older)
    _, _, score_with_history, _ = live_fair_probability(0.5, point, newer)
    assert score_with_history > score_without_history, (
        "el salto de 10-10 a 30-10 debe sumar momentum positivo real"
    )


# --- evaluate_live_limit_arb: gating y construcción de la decisión ---

def test_evaluate_creates_decision_when_favourite_undervalued():
    match = _match()
    tick = _tick(match, market_a=0.55, fair_a=0.72)  # A barato vs su fair pre-match
    point = _point_snapshot(match, tick)  # A domina en vivo -> ajusta fair aún más arriba
    stats = _stats_snapshot(match, tick)

    decision = evaluate_live_limit_arb(match, point, stats)

    assert decision is not None
    assert decision.case == "favourite_undervalued"
    assert decision.favourite_side == "a"
    assert decision.market_leg_side == "a"  # comprar YA la pata barata (favorito)
    assert decision.limit_leg_side == "b"   # límite en la pata contraria
    assert decision.status == "open"
    assert decision.expected_locked_return > 0


def test_evaluate_returns_none_below_min_edge():
    match = _match()
    tick = _tick(match, market_a=0.70, fair_a=0.72)  # edge chico
    point = _point_snapshot(match, tick, points=_points(a_games=2, b_games=2))
    stats = _stats_snapshot(match, tick, total_points_won_a=15, total_points_won_b=15)
    assert evaluate_live_limit_arb(match, point, stats) is None


def test_evaluate_returns_none_when_favourite_undervalued_but_declining():
    """No se compra el dip si además hay evidencia real de deterioro --
    edge barato + tendencia negativa = señal contradictoria, se descarta."""
    match = _match()
    tick = _tick(match, market_a=0.50, fair_a=0.72)  # edge grande
    point = _point_snapshot(match, tick, points=_points(a_games=0, b_games=5))
    stats = _stats_snapshot(
        match, tick, aces_a=0, aces_b=8, double_faults_a=6, double_faults_b=0,
        total_points_won_a=8, total_points_won_b=35,
    )
    assert evaluate_live_limit_arb(match, point, stats) is None


def test_evaluate_returns_none_below_min_games_observed():
    match = _match()
    tick = _tick(match, market_a=0.50, fair_a=0.72)
    point = _point_snapshot(match, tick, points=_points(a_games=1, b_games=0))  # 1 juego
    stats = _stats_snapshot(match, tick)
    assert evaluate_live_limit_arb(match, point, stats) is None


def test_evaluate_returns_none_without_market_or_model_price():
    match = _match()
    tick = ProbabilityTick.create(
        match=match, observed_at=datetime.now(timezone.utc),
        market_price_a=None, market_price_b=None, model_fair_a=None, model_fair_b=None,
    )
    point = _point_snapshot(match, tick)
    stats = _stats_snapshot(match, tick)
    assert evaluate_live_limit_arb(match, point, stats) is None


def test_evaluate_returns_none_on_mismatched_ticks():
    """point y stats deben venir del MISMO tick -- nunca se mezcla
    evidencia de 2 momentos distintos de mercado."""
    match = _match()
    tick1 = _tick(match, offset_s=0)
    tick2 = _tick(match, offset_s=5)
    point = _point_snapshot(match, tick1)
    stats = _stats_snapshot(match, tick2)
    assert evaluate_live_limit_arb(match, point, stats) is None


def test_evaluate_is_idempotent_per_tick_and_strategy_version():
    match = _match()
    tick = _tick(match, market_a=0.55, fair_a=0.72)
    point = _point_snapshot(match, tick)
    stats = _stats_snapshot(match, tick)

    first = evaluate_live_limit_arb(match, point, stats)
    second = evaluate_live_limit_arb(match, point, stats)

    assert first is not None and second is not None
    assert first.id == second.id
    assert LiveLimitArbDecision.select().count() == 1


def test_evaluate_favourite_overvalued_flips_legs():
    match = _match()
    # A sigue siendo favorito pre-match (fair 0.72 > 0.5), pero el mercado
    # lo tiene carísimo -> el favorito está SOBREVALUADO, se apuesta al dog.
    tick = _tick(match, market_a=0.90, fair_a=0.72)
    point = _point_snapshot(match, tick, points=_points(a_games=2, b_games=2))
    stats = _stats_snapshot(match, tick, total_points_won_a=20, total_points_won_b=20)

    decision = evaluate_live_limit_arb(match, point, stats)

    assert decision is not None
    assert decision.case == "favourite_overvalued"
    assert decision.market_leg_side == "b"  # comprar YA la pata barata (underdog)
    assert decision.limit_leg_side == "a"


def test_locked_return_accounts_for_both_leg_fees():
    match = _match()
    tick = _tick(match, market_a=0.40, fair_a=0.72)
    point = _point_snapshot(match, tick)
    stats = _stats_snapshot(match, tick)
    decision = evaluate_live_limit_arb(match, point, stats)
    assert decision is not None
    features = json.loads(decision.features_json)
    # 1 - market - limit - fees, nunca negativo por construcción (se filtra antes).
    expected = max(
        0.0,
        1.0 - decision.market_leg_price - decision.limit_leg_price - features["fees"],
    )
    assert decision.expected_locked_return == pytest.approx(expected, abs=1e-6)


# --- comeback_profile: el bug real encontrado y arreglado ---

def test_comeback_profile_counts_player_regardless_of_side():
    """2026-08-17: bug real de Codex -- la versión original solo miraba
    `Match.player_a_name`. Acá el jugador favorito de HOY jugó como
    `player_b` en un partido previo -- debe contar igual."""
    old_match = _match(
        suffix="old", player_a_name="Underdog Player", player_b_name="Favourite Player",
    )
    old_tick = _tick(old_match)
    old_point = _point_snapshot(old_match, old_tick)
    old_stats = _stats_snapshot(old_match, old_tick)
    LiveLimitArbDecision.create(
        match=old_match, probability_tick=old_tick, point_snapshot=old_point,
        stats_snapshot=old_stats, strategy_version="live_limit_arb_v1",
        decision_at=datetime.now(timezone.utc),
        favourite_side="b",  # Favourite Player era player_b en ESE partido
        case="favourite_undervalued", market_probability=0.5,
        live_fair_probability=0.7, edge=0.2, performance_state="stable",
        performance_score=0.0, market_leg_side="b", market_leg_price=0.5,
        limit_leg_side="a", limit_leg_price=0.4, expected_locked_return=0.05,
        features_json="{}", status="won",
    )

    new_match = _match(suffix="new")  # Favourite Player es player_a acá
    rate, n = comeback_profile(new_match, "a")

    assert n == 1, "debe encontrar la decisión previa aunque el jugador cambió de lado"
    assert rate == 1.0


def test_comeback_profile_does_not_count_when_player_was_the_underdog():
    """El jugador apareció en un partido previo pero como UNDERDOG (no
    favorito) -- esa fila no debe contar para su perfil de remontada
    como favorito."""
    old_match = _match(suffix="old2")
    old_tick = _tick(old_match)
    old_point = _point_snapshot(old_match, old_tick)
    old_stats = _stats_snapshot(old_match, old_tick)
    LiveLimitArbDecision.create(
        match=old_match, probability_tick=old_tick, point_snapshot=old_point,
        stats_snapshot=old_stats, strategy_version="live_limit_arb_v1",
        decision_at=datetime.now(timezone.utc),
        favourite_side="a",  # el OTRO jugador (player_a) era el favorito, no "Underdog Player"
        case="favourite_undervalued", market_probability=0.5,
        live_fair_probability=0.7, edge=0.2, performance_state="stable",
        performance_score=0.0, market_leg_side="a", market_leg_price=0.5,
        limit_leg_side="b", limit_leg_price=0.4, expected_locked_return=0.05,
        features_json="{}", status="won",
    )

    new_match = _match(suffix="new2", player_a_name="Underdog Player", player_b_name="X")
    rate, n = comeback_profile(new_match, "a")

    assert n == 0
    assert rate is None


def test_comeback_profile_fail_closed_with_no_history():
    match = _match()
    rate, n = comeback_profile(match, "a")
    assert (rate, n) == (None, 0)


# --- settle_live_limit_arb_decisions ---

def test_settle_marks_won_when_favourite_side_wins():
    match = _match()
    tick = _tick(match, market_a=0.55, fair_a=0.72)
    point = _point_snapshot(match, tick)
    stats = _stats_snapshot(match, tick)
    decision = evaluate_live_limit_arb(match, point, stats)
    assert decision.status == "open"

    match.winner = decision.favourite_side
    match.save()

    settled = settle_live_limit_arb_decisions()

    assert settled == 1
    refreshed = LiveLimitArbDecision.get_by_id(decision.id)
    assert refreshed.status == "won"
    assert refreshed.settled_at is not None


def test_settle_marks_lost_when_favourite_side_loses():
    match = _match()
    tick = _tick(match, market_a=0.55, fair_a=0.72)
    point = _point_snapshot(match, tick)
    stats = _stats_snapshot(match, tick)
    decision = evaluate_live_limit_arb(match, point, stats)

    other_side = "b" if decision.favourite_side == "a" else "a"
    match.winner = other_side
    match.save()

    settle_live_limit_arb_decisions()
    assert LiveLimitArbDecision.get_by_id(decision.id).status == "lost"


def test_settle_skips_unresolved_matches():
    match = _match()
    tick = _tick(match, market_a=0.55, fair_a=0.72)
    point = _point_snapshot(match, tick)
    stats = _stats_snapshot(match, tick)
    evaluate_live_limit_arb(match, point, stats)  # match.winner sigue None

    assert settle_live_limit_arb_decisions() == 0
