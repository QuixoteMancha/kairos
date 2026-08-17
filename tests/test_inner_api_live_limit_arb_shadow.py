"""Test del endpoint `/api/tennis/live-limit-arb-shadow` -- 2026-08-17,
terminando el trabajo de la sesión de Codex (dejó el worker y el modelo
pero nunca la API)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from database.db import init_db_historical
from database.historical.tennis.models import (
    LiveLimitArbDecision, LivePointSnapshot, LiveStatsSnapshot, Match,
    ProbabilityTick,
)
from inner_api.server import app


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as value:
        yield value


@pytest.fixture(autouse=True)
def clean_state():
    init_db_historical()
    models = (LiveLimitArbDecision, LiveStatsSnapshot, LivePointSnapshot, ProbabilityTick, Match)
    for m in models:
        m.delete().execute()
    yield
    for m in models:
        m.delete().execute()


def test_returns_empty_summary_with_no_decisions(client):
    resp = client.get('/api/tennis/live-limit-arb-shadow')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body == {
        'total': 0, 'open': 0, 'settled': 0, 'wins': 0,
        'market_leg_win_rate': None, 'average_expected_locked_return': None,
        'recent': [],
    }


def test_summarizes_open_and_settled_decisions(client):
    match = Match.create(
        market_id="KX-API", ticker_a="A", ticker_b="B", tour="ATP",
        player_a_name="Fav", player_b_name="Dog", best_of=3,
        detected_at=datetime.now(timezone.utc), flashscore_match_id="fsapi",
        player_a_is_flashscore_p1=True,
    )
    tick = ProbabilityTick.create(
        match=match, observed_at=datetime.now(timezone.utc),
        market_price_a=0.55, market_price_b=0.45, model_fair_a=0.72, model_fair_b=0.28,
    )
    point = LivePointSnapshot.create(
        match=match, probability_tick=tick, flashscore_match_id="fsapi",
        player_a_is_flashscore_p1=True, observed_at=datetime.now(timezone.utc),
        games_observed=4, raw_point_json="{}",
    )
    stats = LiveStatsSnapshot.create(
        match=match, probability_tick=tick, flashscore_match_id="fsapi",
        player_a_is_flashscore_p1=True, observed_at=datetime.now(timezone.utc),
        raw_stats_json="{}",
    )
    LiveLimitArbDecision.create(
        match=match, probability_tick=tick, point_snapshot=point, stats_snapshot=stats,
        strategy_version="live_limit_arb_v1", decision_at=datetime.now(timezone.utc),
        favourite_side="a", case="favourite_undervalued", market_probability=0.55,
        live_fair_probability=0.75, edge=0.2, performance_state="stable",
        performance_score=0.0, market_leg_side="a", market_leg_price=0.55,
        limit_leg_side="b", limit_leg_price=0.35, expected_locked_return=0.06,
        features_json="{}", status="won",
    )

    resp = client.get('/api/tennis/live-limit-arb-shadow')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['total'] == 1
    assert body['settled'] == 1
    assert body['wins'] == 1
    assert body['market_leg_win_rate'] == 1.0
    assert body['average_expected_locked_return'] == pytest.approx(0.06)
    assert body['recent'][0]['market_id'] == 'KX-API'
    assert body['recent'][0]['status'] == 'won'
